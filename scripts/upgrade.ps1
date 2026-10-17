<#
.SYNOPSIS
    Upgrades Ollama, ngrok and/or cloudflared (the service setup has no auto-update, unlike the
    tray app). Tunnels that are not installed are skipped.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\upgrade.ps1                  # everything installed
    powershell -ExecutionPolicy Bypass -File scripts\upgrade.ps1 -Component ollama
#>
[CmdletBinding()]
param(
    [ValidateSet('all', 'ollama', 'ngrok', 'cloudflared')][string]$Component = 'all',
    [string]$Root = 'D:\ollama',
    [string]$ZipUrl = ''
)
. "$PSScriptRoot\common.ps1"
Assert-Admin
if (-not $ZipUrl) { $ZipUrl = $OllamaZipUrl }

if ($Component -in 'all', 'ollama') {
    $before = try { (Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 3).version } catch { 'unknown' }
    Write-Step "Upgrading Ollama (current: $before)"
    # Download first so the service is down only for the extract.
    $zip = Join-Path $env:TEMP 'ollama-windows-amd64.zip'
    Invoke-WebRequest -Uri $ZipUrl -OutFile $zip -UseBasicParsing

    Stop-ServiceSafe OllamaGateway
    Stop-ServiceSafe OllamaServe
    # Keep the current version as bin.previous and extract into a clean bin (no stale CUDA
    # runners); roll back automatically if the new version does not come up.
    $bin = Join-Path $Root 'bin'
    $previous = Join-Path $Root 'bin.previous'
    if (Test-Path $previous) { Remove-Item -Recurse -Force $previous }
    Rename-Item $bin $previous
    Write-Step "Previous version kept in $previous"
    try {
        Expand-Archive -Path $zip -DestinationPath $bin -Force
        Start-Service OllamaServe
        if (-not (Wait-Http 'http://127.0.0.1:11434/api/version' 60)) { throw 'OllamaServe did not answer after the upgrade.' }
    } catch {
        Write-Warning "Upgrade failed: $($_.Exception.Message). Rolling back to $before."
        Stop-ServiceSafe OllamaServe
        if (Test-Path $bin) { Remove-Item -Recurse -Force $bin }
        Rename-Item $previous $bin
        Start-Service OllamaServe
        if (Get-Service OllamaGateway -ErrorAction SilentlyContinue) { Start-Service OllamaGateway }
        throw
    } finally {
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
    }
    if (Get-Service OllamaGateway -ErrorAction SilentlyContinue) { Start-Service OllamaGateway }
    $after = (Invoke-RestMethod 'http://127.0.0.1:11434/api/version').version
    Write-Host "    Ollama $before -> $after"
    Test-OllamaGpu
    if (Get-Service OllamaGateway -ErrorAction SilentlyContinue) {
        if (Wait-Http 'http://127.0.0.1:8000/health/ready' 60) { Write-Host '    Gateway ready.' }
        else { Write-Warning 'Gateway is not ready; check logs\gateway-*.log' }
    }
}

if ($Component -in 'all', 'ngrok') {
    $ngrok = Join-Path $Root 'ngrok\ngrok.exe'
    if ((Get-Service NgrokTunnel -ErrorAction SilentlyContinue) -and (Test-Path $ngrok)) {
        Write-Step "Upgrading ngrok ($(& $ngrok version))"
        Stop-ServiceSafe NgrokTunnel
        & $ngrok update
        Start-Service NgrokTunnel
        Write-Host "    now: $(& $ngrok version)"
    } else {
        Write-Host '    NgrokTunnel service not installed; skipping.'
    }
}

if ($Component -in 'all', 'cloudflared') {
    if (Get-Service Cloudflared -ErrorAction SilentlyContinue) {
        $before = & (Find-Cloudflared) --version
        Write-Step "Upgrading cloudflared ($before)"
        Install-CloudflaredMsi
        Restart-Service Cloudflared
        Write-Host "    now: $(& (Find-Cloudflared) --version)"
        Get-Service Cloudflared | Format-Table Name, Status -AutoSize
    } else {
        Write-Host '    Cloudflared service not installed; skipping.'
    }
}
