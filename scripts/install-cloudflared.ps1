<#
.SYNOPSIS
    Installs cloudflared as a Windows service using a remotely-managed tunnel token.

.DESCRIPTION
    Create the tunnel first: Cloudflare dashboard > Zero Trust > Networks > Tunnels > Create a tunnel
    (Cloudflared), copy the token from the install command, then run this script.
    Use -Http2 if the network blocks outbound UDP 7844 (QUIC).
    Logs go to <LogRoot>\cloudflared\cloudflared.log (rotated by cloudflared, 1 MB x 5).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install-cloudflared.ps1 -Token eyJhIjoi...
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Token,
    [switch]$Http2,
    [string]$LogRoot = 'D:\ollama\logs'
)
. "$PSScriptRoot\common.ps1"
Assert-Admin

$exe = Find-Cloudflared
if (-not $exe) {
    Install-CloudflaredMsi
    Update-SessionPath
    $exe = Find-Cloudflared
    if (-not $exe) { throw 'cloudflared not found after MSI install.' }
}
& $exe --version

if (Get-Service Cloudflared -ErrorAction SilentlyContinue) {
    Write-Step 'Removing existing Cloudflared service'
    & $exe service uninstall
    Start-Sleep -Seconds 2
}

Write-Step 'Installing Cloudflared service'
& $exe service install $Token
if ($LASTEXITCODE -ne 0) { throw "cloudflared service install failed (exit $LASTEXITCODE)" }

# The service otherwise logs only to the Windows Event Log; add a rolling log directory
# (and the http2 protocol if requested) to the service command line.
$logDir = Join-Path $LogRoot 'cloudflared'
New-Item -ItemType Directory -Force $logDir | Out-Null
$extra = "--loglevel info --log-directory `"$logDir`""
if ($Http2) { $extra = "--protocol http2 $extra" }
$key = 'HKLM:\SYSTEM\CurrentControlSet\Services\Cloudflared'
$imagePath = (Get-ItemProperty $key).ImagePath
if ($imagePath -notmatch '--log-directory') {
    Write-Step "Configuring tunnel flags: $extra"
    Set-ItemProperty $key -Name ImagePath -Value ($imagePath -replace ' tunnel ', " tunnel $extra ")
}
Restart-Service Cloudflared

Start-Sleep -Seconds 3
Get-Service Cloudflared | Format-Table Name, Status, StartType -AutoSize

Write-Host @'
Finish in the Cloudflare dashboard:
  1. Tunnel > Public Hostname > Add:
       Subdomain/Domain : llm.<your-domain>
       Path             : ^/(v1|health)(/.*)?$
       Service          : HTTP  127.0.0.1:8000     (never add port 8001)
  2. Security > WAF > Custom rules > Edit expression, action Block:
       (http.host eq "llm.<your-domain>" and starts_with(http.request.uri.path, "/v1")
        and not any(lower(http.request.headers.names[*])[*] eq "authorization"))
  3. Security > WAF > Rate limiting rules: e.g. 120 requests / 10 s per IP on llm.<your-domain>
  4. Security > Bots: turn OFF Bot Fight Mode; Rules > Configuration Rules for llm.<your-domain>:
       Browser Integrity Check OFF  (otherwise SDK clients receive HTML challenge pages)
  5. Test from outside:  curl https://llm.<your-domain>/health
'@
