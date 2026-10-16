<#
.SYNOPSIS
    One-screen overview: services, dependency health, GPU, today's usage, recent requests,
    recent warnings/errors and where the log files are. No admin rights needed.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\status.ps1
    powershell -ExecutionPolicy Bypass -File scripts\status.ps1 -Watch 5      # refresh every 5 s
#>
[CmdletBinding()]
param(
    [int]$Watch = 0,
    [int]$Requests = 15,
    [string]$AdminUrl = 'http://127.0.0.1:8001',
    [string]$OllamaLogs = 'D:\ollama\logs'
)
$ErrorActionPreference = 'Continue'
$project = Split-Path $PSScriptRoot -Parent
$logDir = Join-Path $project 'logs'

$adminKey = $null
$envFile = Join-Path $project '.env'
if (Test-Path $envFile) {
    $m = Select-String -Path $envFile -Pattern '^ADMIN_KEY=(.+)$' -ErrorAction SilentlyContinue
    if ($m) { $adminKey = $m.Matches[0].Groups[1].Value.Trim() }
}
$headers = @{ Authorization = "Bearer $adminKey" }

function Section([string]$Title) { Write-Host ''; Write-Host "== $Title ==" -ForegroundColor Cyan }

function Latest-Log([string]$Prefix, [int]$Count = 1) {
    Get-ChildItem (Join-Path $logDir "$Prefix-*.log") -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last $Count
}

function Show-Status {
    Write-Host ("ollama-provider status  {0:yyyy-MM-dd HH:mm:ss}" -f (Get-Date)) -ForegroundColor White

    Section 'Services'
    foreach ($name in 'OllamaServe', 'OllamaGateway', 'NgrokTunnel', 'Cloudflared') {
        $svc = Get-Service $name -ErrorAction SilentlyContinue
        if (-not $svc) { Write-Host ("  {0,-14} not installed" -f $name) -ForegroundColor DarkGray; continue }
        $color = if ($svc.Status -eq 'Running') { 'Green' } else { 'Red' }
        Write-Host ("  {0,-14} {1,-10} {2}" -f $name, $svc.Status, $svc.StartType) -ForegroundColor $color
    }

    Section 'Dependency health'
    $status = $null
    if (-not $adminKey) {
        Write-Host '  ADMIN_KEY not found in .env; cannot query the admin API.' -ForegroundColor Yellow
    } else {
        try { $status = Invoke-RestMethod "$AdminUrl/admin/status" -Headers $headers -TimeoutSec 15 }
        catch { Write-Host "  Admin API unreachable: $($_.Exception.Message)" -ForegroundColor Red }
    }
    if ($status) {
        foreach ($p in $status.components.PSObject.Properties) {
            $color = switch ($p.Value.status) { 'ok' { 'Green' } 'absent' { 'DarkGray' } 'warn' { 'Yellow' } default { 'Red' } }
            Write-Host ("  {0,-22} {1,-7} {2}" -f $p.Name, $p.Value.status, $p.Value.detail) -ForegroundColor $color
        }
        $c = $status.gateway.concurrency
        Write-Host ("  GPU slots: {0}/{1} busy, {2} waiting   uptime {3:N0} min" -f $c.active, $c.global_limit, $c.waiting, ($status.gateway.uptime_s / 60))
        foreach ($m in $status.ollama.loaded_models) {
            $pct = if ($m.size) { [math]::Round(100 * $m.size_vram / $m.size) } else { 0 }
            Write-Host ("  loaded: {0}  {1}% GPU  ctx {2}" -f $m.name, $pct, $m.context_length)
        }

        Section 'Usage today (per key)'
        $since = (Get-Date).Date.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss')
        try {
            $usage = Invoke-RestMethod "$AdminUrl/admin/usage?group_by=key&since=$since" -Headers $headers -TimeoutSec 10
            if ($usage.data) {
                $usage.data | Format-Table key_name, requests, errors, prompt_tokens, completion_tokens, avg_latency_ms -AutoSize | Out-Host
            } else { Write-Host '  no requests yet today' }
        } catch { Write-Host "  usage unavailable: $($_.Exception.Message)" -ForegroundColor Yellow }
    }

    Section "Recent requests (last $Requests)"
    $access = Latest-Log 'access'
    if ($access) {
        $rows = Get-Content $access.FullName -Tail $Requests | ForEach-Object {
            try { $_ | ConvertFrom-Json } catch { $null }
        } | Where-Object { $_ }
        $rows | Select-Object @{n = 'time'; e = { ([datetime]$_.ts).ToString('HH:mm:ss') } }, ip, key, path, model, status,
            @{n = 'ms'; e = { $_.duration_ms } }, @{n = 'tokens'; e = { $_.total_tokens } }, error |
            Format-Table -AutoSize | Out-Host
    } else { Write-Host '  no access log yet' }

    Section 'Recent warnings / errors'
    $files = @(Latest-Log 'gateway' 2) | ForEach-Object { $_.FullName }
    $hits = if ($files) { Select-String -Path $files -Pattern ' (WARNING|ERROR|CRITICAL) ' | Select-Object -Last 10 } else { @() }
    if ($hits) { $hits | ForEach-Object { Write-Host "  $($_.Line)" -ForegroundColor Yellow } }
    else { Write-Host '  none' -ForegroundColor Green }

    Section 'Log files'
    Write-Host "  activity (JSON)  : $logDir\access-YYYY-MM-DD.log"
    Write-Host "  gateway + audit  : $logDir\gateway-YYYY-MM-DD.log"
    Write-Host "  dependency health: $logDir\health-YYYY-MM-DD.log"
    Write-Host "  gateway crashes  : $logDir\OllamaGateway.err.log"
    Write-Host "  ollama           : $OllamaLogs\OllamaServe.err.log"
    Write-Host "  ngrok tunnel     : $OllamaLogs\ngrok\NgrokTunnel.out.log"
    Write-Host "  cloudflared      : $OllamaLogs\cloudflared\cloudflared.log"
    Write-Host "  live tail        : Get-Content $logDir\access-$(Get-Date -Format yyyy-MM-dd).log -Wait -Tail 20"
}

do {
    if ($Watch -gt 0) { Clear-Host }
    Show-Status
    if ($Watch -gt 0) { Start-Sleep -Seconds $Watch }
} while ($Watch -gt 0)
