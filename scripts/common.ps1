# Shared helpers for the install/upgrade scripts. Dot-source it: . "$PSScriptRoot\common.ps1"
# Keep this file ASCII-only (Windows PowerShell 5.1 reads BOM-less scripts as ANSI).

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest's progress bar is very slow on PS 5.1
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$OllamaZipUrl = 'https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip'
$WinSWUrl = 'https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe'

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Assert-Admin {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this script from an elevated PowerShell (Run as administrator).'
    }
}

function Update-SessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Get-WinSW([string]$ToolsDir) {
    # WinSW v2 (maintained service wrapper). Each service gets its own renamed copy + XML.
    $target = Join-Path $ToolsDir 'WinSW-x64.exe'
    if (-not (Test-Path $target)) {
        New-Item -ItemType Directory -Force $ToolsDir | Out-Null
        Write-Step "Downloading WinSW from $WinSWUrl"
        Invoke-WebRequest -Uri $WinSWUrl -OutFile $target -UseBasicParsing
    }
    return $target
}

function Find-Cloudflared {
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @("${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe", "$env:ProgramFiles\cloudflared\cloudflared.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Install-CloudflaredMsi {
    # Direct MSI from GitHub releases (does not depend on winget). Also used for upgrades.
    $msiUrl = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.msi'
    $msi = Join-Path $env:TEMP 'cloudflared-windows-amd64.msi'
    Write-Step "Downloading $msiUrl"
    Invoke-WebRequest -Uri $msiUrl -OutFile $msi -UseBasicParsing
    $proc = Start-Process msiexec.exe -ArgumentList '/i', "`"$msi`"", '/qn', '/norestart' -Wait -PassThru
    Remove-Item $msi -Force -ErrorAction SilentlyContinue
    if ($proc.ExitCode -notin 0, 3010) { throw "cloudflared MSI install failed (exit $($proc.ExitCode))" }
}

function Stop-ServiceSafe([string]$Name, [int]$TimeoutSec = 60) {
    # Never block forever: stop, wait, and as a last resort kill the service process tree.
    $svc = Get-Service $Name -ErrorAction SilentlyContinue
    if (-not $svc -or $svc.Status -eq 'Stopped') { return }
    Write-Step "Stopping $Name"
    sc.exe stop $Name | Out-Null
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        if ((Get-Service $Name).Status -eq 'Stopped') { return }
        Start-Sleep -Milliseconds 250
    }
    $hostPid = (Get-CimInstance Win32_Service -Filter "Name='$Name'").ProcessId
    Write-Warning "$Name did not stop within $TimeoutSec s; killing process tree of PID $hostPid"
    if ($hostPid) { taskkill /PID $hostPid /T /F | Out-Null }
    Start-Sleep -Seconds 2
}

function Remove-ServiceIfExists([string]$Name) {
    if (-not (Get-Service $Name -ErrorAction SilentlyContinue)) { return }
    Write-Step "Removing existing service $Name"
    Stop-ServiceSafe $Name
    sc.exe delete $Name | Out-Null
    for ($i = 0; $i -lt 40 -and (Get-Service $Name -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 250 }
}

function Install-WinSWService {
    param(
        [Parameter(Mandatory)][string]$WinSW,
        [Parameter(Mandatory)][string]$ServiceDir,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Exe,
        [string]$Arguments = '',
        [Parameter(Mandatory)][string]$Directory,
        [string]$DisplayName = $Name,
        [string]$Description = '',
        [Collections.IDictionary]$Environment = @{},
        [Parameter(Mandatory)][string]$LogDir,
        [string]$DependsOn = '',
        [int]$StopTimeoutSec = 15
    )
    Remove-ServiceIfExists $Name
    New-Item -ItemType Directory -Force $ServiceDir, $LogDir | Out-Null

    $x = { param($v) [Security.SecurityElement]::Escape([string]$v) }
    $lines = @(
        '<service>',
        "  <id>$(& $x $Name)</id>",
        "  <name>$(& $x $DisplayName)</name>",
        "  <description>$(& $x $Description)</description>",
        "  <executable>$(& $x $Exe)</executable>",
        "  <arguments>$(& $x $Arguments)</arguments>",
        "  <workingdirectory>$(& $x $Directory)</workingdirectory>",
        '  <startmode>Automatic</startmode>'
    )
    if ($DependsOn) { $lines += "  <depend>$(& $x $DependsOn)</depend>" }
    foreach ($k in $Environment.Keys) { $lines += "  <env name=`"$(& $x $k)`" value=`"$(& $x $Environment[$k])`"/>" }
    $lines += @(
        # Graceful stop: Ctrl+C to the app, then kill the tree after the timeout.
        "  <stoptimeout>$StopTimeoutSec sec</stoptimeout>",
        '  <stopparentprocessfirst>true</stopparentprocessfirst>',
        # Crash recovery through the SCM failure actions.
        '  <onfailure action="restart" delay="5 sec"/>',
        '  <onfailure action="restart" delay="10 sec"/>',
        '  <onfailure action="restart" delay="30 sec"/>',
        '  <resetfailure>1 hour</resetfailure>',
        "  <logpath>$(& $x $LogDir)</logpath>",
        '  <log mode="roll-by-size">',
        '    <sizeThreshold>10240</sizeThreshold>',
        '    <keepFiles>5</keepFiles>',
        '  </log>',
        '</service>'
    )
    $wrapper = Join-Path $ServiceDir "$Name.exe"
    Copy-Item $WinSW $wrapper -Force
    Set-Content -Path (Join-Path $ServiceDir "$Name.xml") -Value $lines -Encoding ASCII

    Write-Step "Installing service $Name"
    & $wrapper install | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "WinSW install of $Name failed (exit $LASTEXITCODE)" }
}

function Wait-Http([string]$Url, [int]$TimeoutSec = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-RestMethod -Uri $Url -TimeoutSec 3 | Out-Null
            return $true
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $false
}

function Install-OllamaZip([string]$BinDir, [string]$ZipUrl) {
    $zip = Join-Path $env:TEMP 'ollama-windows-amd64.zip'
    Write-Step "Downloading $ZipUrl (large, CUDA libraries included)"
    Invoke-WebRequest -Uri $ZipUrl -OutFile $zip -UseBasicParsing
    # Drop old GPU libraries so stale CUDA runners never mix with new ones.
    if (Test-Path (Join-Path $BinDir 'lib')) { Remove-Item -Recurse -Force (Join-Path $BinDir 'lib') }
    Write-Step "Extracting to $BinDir"
    Expand-Archive -Path $zip -DestinationPath $BinDir -Force
    Remove-Item $zip -Force
}

function Test-OllamaGpu([string]$BaseUrl = 'http://127.0.0.1:11434') {
    $tags = Invoke-RestMethod "$BaseUrl/api/tags"
    if (-not $tags.models) { Write-Warning 'No models installed yet.'; return }
    $model = $tags.models[0].name
    Write-Step "Loading $model to check GPU placement"
    $body = @{ model = $model } | ConvertTo-Json
    Invoke-RestMethod -Method Post "$BaseUrl/api/generate" -Body $body -ContentType 'application/json' -TimeoutSec 300 | Out-Null
    foreach ($m in (Invoke-RestMethod "$BaseUrl/api/ps").models) {
        $pct = [math]::Round(100 * $m.size_vram / $m.size)
        Write-Host ("    {0}: {1}% GPU, context {2}, VRAM {3:N1} GB" -f $m.name, $pct, $m.context_length, ($m.size_vram / 1GB))
        if ($pct -lt 100) {
            Write-Warning 'Model does not fit fully in VRAM. Re-run with -NumParallel 2 and set GLOBAL_CONCURRENCY=2 in .env.'
        }
    }
}
