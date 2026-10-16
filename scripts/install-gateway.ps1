<#
.SYNOPSIS
    Installs the API gateway as the Windows service "OllamaGateway" (depends on OllamaServe).

.DESCRIPTION
    - Creates .env from .env.example (with a generated ADMIN_KEY) if missing
    - Ensures .venv exists and is based on a Python that LocalSystem can read
    - Restricts .env and data\ to SYSTEM, Administrators and the current user
    - Registers and starts OllamaGateway via WinSW (public :8000, admin :8001, loopback only)

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install-gateway.ps1
#>
[CmdletBinding()]
param(
    [string]$Root = 'D:\ollama',
    [string]$Python = 'C:\Program Files\Python311\python.exe'
)
. "$PSScriptRoot\common.ps1"
Assert-Admin

$project = Split-Path $PSScriptRoot -Parent
$venvPython = Join-Path $project '.venv\Scripts\python.exe'
$envFile = Join-Path $project '.env'
$dataDir = Join-Path $project 'data'
$winsw = Get-WinSW (Join-Path $Root 'tools')
if (-not (Get-Service OllamaServe -ErrorAction SilentlyContinue)) {
    throw 'Service OllamaServe not found. Run scripts\install-ollama.ps1 first.'
}

# 1. Virtual environment
if (-not (Test-Path $venvPython)) {
    Write-Step 'Creating .venv with uv'
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'uv not found on PATH.' }
    Push-Location $project
    try { uv sync --python $Python } finally { Pop-Location }
}
$venvHome = (Get-Content (Join-Path $project '.venv\pyvenv.cfg') | Where-Object { $_ -match '^home\s*=' }) -replace '^home\s*=\s*', ''
if ($venvHome -like 'C:\Users\*') {
    Write-Warning ".venv is based on $venvHome (inside a user profile). Recreate it with: uv venv --python `"$Python`"; uv sync"
}
# The service runs the base interpreter directly with the venv's site-packages on PYTHONPATH.
# .venv\Scripts\python.exe is only a launcher that spawns a second python.exe; a single
# process keeps Ctrl+C (graceful stop) and crash detection straightforward.
$basePython = Join-Path $venvHome 'python.exe'
$sitePackages = Join-Path $project '.venv\Lib\site-packages'

# 2. .env with an admin key
if (-not (Test-Path $envFile)) {
    Write-Step 'Creating .env from .env.example'
    Copy-Item (Join-Path $project '.env.example') $envFile
}
$lines = Get-Content $envFile
if ($lines -match '^ADMIN_KEY=\s*$') {
    $adminKey = (& $venvPython -m gateway.cli gen-admin-key).Trim()
    $lines = $lines -replace '^ADMIN_KEY=\s*$', "ADMIN_KEY=$adminKey"
    Set-Content -Path $envFile -Value $lines -Encoding ASCII
    Write-Step 'Generated ADMIN_KEY in .env'
}

# 3. Lock down secrets (SYSTEM, Administrators, current user)
New-Item -ItemType Directory -Force $dataDir, (Join-Path $project 'logs') | Out-Null
# Use SIDs: names like WORKGROUP\user do not resolve for local accounts.
$me = '*' + [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
# The service runs as LocalService (not SYSTEM): the project folder stays editable for
# developers/agents, so code changes there must never run with SYSTEM rights.
# LocalService gets: read on code, .venv and .env; modify on data\ and logs\ only.
$logDir = Join-Path $project 'logs'
icacls $envFile /inheritance:r /grant:r "${SidSystem}:F" "${SidAdmins}:F" "${me}:F" "${SidLocalService}:R" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "icacls failed on $envFile" }
icacls $dataDir /inheritance:r /grant:r "${SidSystem}:(OI)(CI)F" "${SidAdmins}:(OI)(CI)F" "${me}:(OI)(CI)F" `
    "${SidLocalService}:(OI)(CI)M" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "icacls failed on $dataDir" }
# Existing files (e.g. gateway.db) must inherit from data\ instead of keeping their old ACL.
if (Get-ChildItem $dataDir -Force) { icacls "$dataDir\*" /reset /T /C | Out-Null }
icacls $project /grant "${SidLocalService}:(OI)(CI)RX" | Out-Null
icacls $logDir /grant "${SidLocalService}:(OI)(CI)M" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "icacls failed on $logDir" }

# 4. Service
# The app writes its own daily logs (logs\gateway-, access-, health-*.log); console output is
# off so WinSW's OllamaGateway.out/err.log only catch crashes and startup failures.
$environment = [ordered]@{
    PYTHONUNBUFFERED = '1'
    PYTHONUTF8       = '1'
    PYTHONPATH       = "$project;$sitePackages"
    LOG_CONSOLE      = 'false'
}
Install-WinSWService -WinSW $winsw -ServiceDir (Join-Path $Root 'services') -Name 'OllamaGateway' `
    -Exe $basePython -Arguments '-m gateway' -Directory $project -DisplayName 'Ollama Provider Gateway' `
    -Description 'OpenAI-compatible API gateway (API keys, rate limits, usage logs) in front of Ollama' `
    -Environment $environment -LogDir $logDir -DependsOn 'OllamaServe' -StopTimeoutSec 15 `
    -ServiceAccount 'NT AUTHORITY\LocalService'

Write-Step 'Starting OllamaGateway'
Start-Service OllamaGateway
if (-not (Wait-Http 'http://127.0.0.1:8000/health' 60)) {
    throw "Gateway did not answer on 127.0.0.1:8000. Check $project\logs\OllamaGateway.*.log"
}
Write-Host '    Public API : http://127.0.0.1:8000  (route this through Cloudflare Tunnel)'
Write-Host '    Admin API  : http://127.0.0.1:8001  (local only; ADMIN_KEY is in .env)'
Write-Host ''
Write-Host 'Create a client key:' -ForegroundColor Green
Write-Host '    .venv\Scripts\python.exe -m gateway.cli create-key --name client-a'
Write-Host 'Next: scripts\install-cloudflared.ps1 -Token <tunnel token>'
