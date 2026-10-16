<#
.SYNOPSIS
    Exposes the public API (127.0.0.1:8000) through ngrok's free static domain, as the Windows
    service "NgrokTunnel" (WinSW: auto-start, restart on crash, rotated logs).

.DESCRIPTION
    Free alternative to Cloudflare Tunnel (no paid domain needed). Before running:
      1. Sign up at https://dashboard.ngrok.com (free plan)
      2. Copy your authtoken: Getting Started > Your Authtoken
      3. Claim your free static domain: Universal Gateway > Domains (e.g. xyz.ngrok-free.app)
    Only port 8000 is exposed; the admin API (8001) and Ollama (11434) stay local.
    The token is stored in <Root>\ngrok\ngrok.yml, readable only by SYSTEM, Administrators
    and the installing user.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install-ngrok.ps1 -AuthToken <token> -Domain xyz.ngrok-free.app
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$AuthToken,
    [Parameter(Mandatory)][string]$Domain,
    [string]$Root = 'D:\ollama',
    [string]$SourceExe = 'C:\ngrok\ngrok.exe'
)
. "$PSScriptRoot\common.ps1"
Assert-Admin

$Domain = ($Domain -replace '^https?://', '').TrimEnd('/')
$project = Split-Path $PSScriptRoot -Parent
$ngrokDir = Join-Path $Root 'ngrok'
$exe = Join-Path $ngrokDir 'ngrok.exe'
$config = Join-Path $ngrokDir 'ngrok.yml'
$logDir = Join-Path $Root 'logs\ngrok'
New-Item -ItemType Directory -Force $ngrokDir, $logDir | Out-Null
$winsw = Get-WinSW (Join-Path $Root 'tools')

# 1. Binary: private copy outside any user profile (the service never depends on C:\ngrok)
Stop-ServiceSafe NgrokTunnel
if (Test-Path $SourceExe) {
    Write-Step "Copying $SourceExe -> $exe"
    Copy-Item $SourceExe $exe -Force
} elseif (-not (Test-Path $exe)) {
    $zip = Join-Path $env:TEMP 'ngrok.zip'
    Write-Step 'Downloading ngrok'
    Invoke-WebRequest -Uri 'https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip' -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $ngrokDir -Force
    Remove-Item $zip -Force
}
& $exe version

# 2. Config (contains the authtoken -> locked down like .env)
Write-Step "Writing $config"
Set-Content -Path $config -Encoding ASCII -Value @(
    'version: "3"',
    'agent:',
    "  authtoken: $AuthToken",
    # The local inspector (127.0.0.1:4040) records full requests incl. Authorization headers
    # and prompts, readable by any local user: keep it off.
    '  web_addr: false',
    'endpoints:',
    '  - name: ollama-provider',
    "    url: https://$Domain",
    '    upstream:',
    '      url: http://127.0.0.1:8000'
)
$me = '*' + [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
icacls $config /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' "${me}:F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "icacls failed on $config" }
& $exe config check --config $config
if ($LASTEXITCODE -ne 0) { throw 'ngrok config check failed' }

# 3. Service
Install-WinSWService -WinSW $winsw -ServiceDir (Join-Path $Root 'services') -Name 'NgrokTunnel' -Exe $exe `
    -Arguments "start --all --config `"$config`" --log stdout --log-format logfmt" -Directory $ngrokDir `
    -DisplayName 'ngrok Tunnel (ollama-provider)' `
    -Description "Public HTTPS endpoint https://$Domain -> 127.0.0.1:8000" -LogDir $logDir -StopTimeoutSec 10
Write-Step 'Starting NgrokTunnel'
Start-Service NgrokTunnel

# 4. Let the gateway health monitor watch the tunnel service
$envFile = Join-Path $project '.env'
if (Test-Path $envFile) {
    $lines = @(Get-Content $envFile | Where-Object { $_ -notmatch '^WATCH_SERVICES=' })
    $lines += 'WATCH_SERVICES=OllamaServe,NgrokTunnel'
    Set-Content -Path $envFile -Value $lines -Encoding ASCII
    if (Get-Service OllamaGateway -ErrorAction SilentlyContinue) { Restart-Service OllamaGateway }
}

# 5. Verify from the public side
$url = "https://$Domain/health"
Write-Step "Checking $url"
$ok = $false
for ($i = 0; $i -lt 20 -and -not $ok; $i++) {
    try {
        $r = Invoke-RestMethod $url -Headers @{ 'ngrok-skip-browser-warning' = '1' } -TimeoutSec 10
        $ok = $r.status -eq 'ok'
    } catch { Start-Sleep -Seconds 2 }
}
if ($ok) {
    Write-Host "    Public API is live: https://$Domain/v1" -ForegroundColor Green
} else {
    Write-Warning "Could not reach $url yet. Check $logDir\NgrokTunnel.out.log"
}
Write-Host ''
Write-Host 'Clients use:  base_url = "https://' -NoNewline; Write-Host "$Domain/v1`"   api_key = sk-... (gateway.cli create-key)"
