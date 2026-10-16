<#
.SYNOPSIS
    Installs standalone Ollama as the Windows service "OllamaServe" (auto-start, no login needed).

.DESCRIPTION
    - Downloads ollama-windows-amd64.zip into <Root>\bin (independent of the per-user tray app)
    - Copies existing models into <Root>\models (the source is left untouched)
    - Stops the tray app and disables its autostart (or uninstalls it with -UninstallTrayApp)
    - Registers OllamaServe via WinSW, bound to 127.0.0.1 only, and checks the model fits in VRAM

.EXAMPLE
    # elevated PowerShell, from the project folder
    powershell -ExecutionPolicy Bypass -File scripts\install-ollama.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install-ollama.ps1 -NumParallel 2 -SkipDownload
#>
[CmdletBinding()]
param(
    [string]$Root = 'D:\ollama',
    [string]$SourceModels = 'C:\Users\User\.ollama\models',
    [int]$NumParallel = 4,
    [int]$ContextLength = 8192,
    [string]$ZipUrl = '',
    [switch]$SkipDownload,
    [switch]$UninstallTrayApp
)
. "$PSScriptRoot\common.ps1"
Assert-Admin
if (-not $ZipUrl) { $ZipUrl = $OllamaZipUrl }

$bin = Join-Path $Root 'bin'
$models = Join-Path $Root 'models'
$logs = Join-Path $Root 'logs'
$tools = Join-Path $Root 'tools'
$services = Join-Path $Root 'services'
New-Item -ItemType Directory -Force $bin, $models, $logs, $tools, $services | Out-Null
$winsw = Get-WinSW $tools

# 1. Stop anything holding port 11434 (tray app or an earlier service run)
$gatewayInstalled = [bool](Get-Service OllamaGateway -ErrorAction SilentlyContinue)
Stop-ServiceSafe OllamaGateway
Stop-ServiceSafe OllamaServe
Write-Step 'Stopping the Ollama tray app'
Get-Process -Name 'ollama app' -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process -Name 'ollama' -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and -not $_.Path.StartsWith($bin, [StringComparison]::OrdinalIgnoreCase) } |
    Stop-Process -Force
Start-Sleep -Seconds 2

# 2. Binary
if ($SkipDownload -and (Test-Path (Join-Path $bin 'ollama.exe'))) {
    Write-Step 'Keeping existing Ollama binary (-SkipDownload)'
} else {
    Install-OllamaZip -BinDir $bin -ZipUrl $ZipUrl
}

# 3. Models (robocopy is incremental; exit codes below 8 mean success)
if (Test-Path $SourceModels) {
    Write-Step "Copying models $SourceModels -> $models"
    robocopy $SourceModels $models /E /R:1 /W:1 /NFL /NDL /NP /NJH | Out-Host
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed (exit $LASTEXITCODE)" }
} else {
    Write-Warning "Source models folder $SourceModels not found; skipping copy."
}

# 4. Tray app autostart (it would fight the service for port 11434 at login)
$startupLinks = Get-ChildItem 'C:\Users\*\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\Ollama.lnk' -ErrorAction SilentlyContinue
foreach ($link in $startupLinks) {
    Write-Step "Disabling tray autostart: $($link.FullName)"
    Move-Item $link.FullName "$($link.FullName).disabled" -Force
}
if ($UninstallTrayApp) {
    foreach ($uninstaller in (Get-ChildItem 'C:\Users\*\AppData\Local\Programs\Ollama\unins*.exe' -ErrorAction SilentlyContinue)) {
        Write-Step "Uninstalling tray app: $($uninstaller.FullName)"
        Start-Process -FilePath $uninstaller.FullName -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait
    }
}

# 5. Service
$environment = [ordered]@{
    OLLAMA_HOST              = '127.0.0.1:11434'
    OLLAMA_MODELS            = $models
    OLLAMA_KEEP_ALIVE        = '-1'
    OLLAMA_NUM_PARALLEL      = "$NumParallel"
    OLLAMA_MAX_LOADED_MODELS = '1'
    OLLAMA_MAX_QUEUE         = '16'
    OLLAMA_FLASH_ATTENTION   = '1'
    OLLAMA_KV_CACHE_TYPE     = 'q8_0'
    OLLAMA_CONTEXT_LENGTH    = "$ContextLength"
}
Install-WinSWService -WinSW $winsw -ServiceDir $services -Name 'OllamaServe' -Exe (Join-Path $bin 'ollama.exe') `
    -Arguments 'serve' -Directory $bin -DisplayName 'Ollama Server' `
    -Description 'Ollama model server (loopback only) for ollama-provider' `
    -Environment $environment -LogDir $logs -StopTimeoutSec 10

Write-Step 'Starting OllamaServe'
Start-Service OllamaServe
if (-not (Wait-Http 'http://127.0.0.1:11434/api/version' 60)) {
    throw "OllamaServe did not answer on 127.0.0.1:11434. Check $logs\OllamaServe.*.log"
}
$version = (Invoke-RestMethod 'http://127.0.0.1:11434/api/version').version
Write-Host "    Ollama $version is running as a service."
Test-OllamaGpu
if ($gatewayInstalled) { Start-Service OllamaGateway }

Write-Host ''
Write-Host 'Done. Next: scripts\install-gateway.ps1' -ForegroundColor Green
Write-Host "Keep GLOBAL_CONCURRENCY in .env equal to NumParallel ($NumParallel)."
