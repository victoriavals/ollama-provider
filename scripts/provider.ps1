<#
.SYNOPSIS
    Stops or starts the whole provider by hand (tunnel, gateway and Ollama).

.DESCRIPTION
    stop    Takes the API offline: the tunnel first (no new requests), then OllamaGateway, then
            OllamaServe (frees the GPU). The services are switched to Manual, so they stay off after
            a reboot or power cut until "start" is run. -UntilReboot keeps them on Automatic.
    start   Switches the services back to Automatic, starts them in order and waits until the
            gateway answers (locally and, when PUBLIC_BASE_URL is set, through the tunnel).
    status  Shows each service and whether the API answers. Does not need an elevated shell.

    The dashboard (port 8001) is part of the gateway, so it is offline while the provider is stopped.
    SSH and Tailscale are not touched.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\provider.ps1 stop
    powershell -ExecutionPolicy Bypass -File scripts\provider.ps1 start
    powershell -ExecutionPolicy Bypass -File scripts\provider.ps1 status
    powershell -ExecutionPolicy Bypass -File scripts\provider.ps1 stop -UntilReboot
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)][ValidateSet('stop', 'start', 'status')][string]$Action,
    [switch]$UntilReboot,
    [int]$Port = 8000
)
. "$PSScriptRoot\common.ps1"

# Start order. Stop runs it backwards: the gateway depends on OllamaServe, and the tunnel goes first
# so no new request arrives while the gateway finishes the running ones.
$Order = @('OllamaServe', 'OllamaGateway', 'NgrokTunnel', 'Cloudflared')
$Installed = @($Order | Where-Object { Get-Service $_ -ErrorAction SilentlyContinue })
$LocalReady = "http://127.0.0.1:$Port/health/ready"

function Get-PublicHealthUrl {
    # PUBLIC_BASE_URL (e.g. https://<domain>/v1) lives in the ACL-protected .env; never print it.
    try {
        $line = Get-Content (Join-Path (Split-Path $PSScriptRoot -Parent) '.env') -ErrorAction Stop |
            Where-Object { $_ -match '^\s*PUBLIC_BASE_URL\s*=\s*\S' } | Select-Object -First 1
    } catch { return $null }
    if (-not $line) { return $null }
    $base = ($line -split '=', 2)[1].Trim().Trim('"').TrimEnd('/')
    return ($base -replace '/v1$', '') + '/health'
}

function Test-PublicHealth([string]$Url) {
    try {
        # ngrok's free plan shows a browser warning page unless this header is sent
        $r = Invoke-RestMethod -Uri $Url -Headers @{ 'ngrok-skip-browser-warning' = '1' } -TimeoutSec 10
        return ($r.status -eq 'ok')
    } catch { return $false }
}

function Show-Status {
    foreach ($name in $Installed) {
        $svc = Get-Service $name
        '{0,-14} {1,-8} start={2}' -f $name, $svc.Status, $svc.StartType
    }
    $local = try { (Invoke-RestMethod -Uri $LocalReady -TimeoutSec 3).status } catch { 'offline' }
    'API (local)    {0}' -f $local
    $public = Get-PublicHealthUrl
    if ($public) {
        'API (public)   {0}' -f $(if (Test-PublicHealth $public) { 'ok' } else { 'offline' })
    }
}

switch ($Action) {
    'status' {
        Show-Status
    }
    'stop' {
        Assert-Admin
        $reverse = @($Installed)
        [array]::Reverse($reverse)
        foreach ($name in $reverse) {
            if (-not $UntilReboot) { Set-Service -Name $name -StartupType Manual }
            Stop-ServiceSafe $name
        }
        if ($UntilReboot) {
            Write-Step 'Provider stopped. It starts again after the next reboot (services left on Automatic).'
        } else {
            Write-Step 'Provider stopped and set to Manual: it stays off after a reboot until you run "provider.ps1 start".'
        }
        Show-Status
    }
    'start' {
        Assert-Admin
        foreach ($name in $Installed) {
            Set-Service -Name $name -StartupType Automatic
            if ((Get-Service $name).Status -ne 'Running') {
                Write-Step "Starting $name"
                Start-Service $name
            }
        }
        Write-Step 'Waiting for the gateway'
        if (-not (Wait-Http $LocalReady 90)) {
            Show-Status
            Write-Warning "The gateway did not become ready within 90 s. Check logs\OllamaGateway.err.log and D:\ollama\logs\OllamaServe.err.log."
            exit 1
        }
        $public = Get-PublicHealthUrl
        if ($public) {
            $deadline = (Get-Date).AddSeconds(45)
            while (-not (Test-PublicHealth $public) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
        }
        Write-Step 'Provider started (services on Automatic). The model finishes loading in the background (~10-40 s).'
        Show-Status
    }
}
