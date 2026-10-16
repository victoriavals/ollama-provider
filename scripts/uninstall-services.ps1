<#
.SYNOPSIS
    Stops and removes the OllamaGateway and OllamaServe services (and optionally the tunnel:
    NgrokTunnel and/or Cloudflared). Files, models, the database and .env are left in place.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\uninstall-services.ps1 [-IncludeNgrok] [-IncludeCloudflared]
#>
[CmdletBinding()]
param(
    [string]$Root = 'D:\ollama',
    [switch]$IncludeNgrok,
    [switch]$IncludeCloudflared
)
. "$PSScriptRoot\common.ps1"
Assert-Admin

$names = @('OllamaGateway', 'OllamaServe')
if ($IncludeNgrok) { $names = @('NgrokTunnel') + $names }
foreach ($name in $names) {
    Remove-ServiceIfExists $name
    Remove-Item (Join-Path $Root "services\$name.exe"), (Join-Path $Root "services\$name.xml") -Force -ErrorAction SilentlyContinue
}

if ($IncludeCloudflared -and (Get-Service Cloudflared -ErrorAction SilentlyContinue)) {
    Write-Step 'Removing Cloudflared service'
    & (Find-Cloudflared) service uninstall
}
Write-Host 'Done. To bring back the tray app autostart, rename Ollama.lnk.disabled in the Startup folder.'
