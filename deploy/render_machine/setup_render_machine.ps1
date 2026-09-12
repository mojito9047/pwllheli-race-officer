<#
Copyright © 2026 CapeNet Ltd. All Rights Reserved.

One-time setup for a 3D replay render machine: make the virtual environment,
install what the renderer needs, put a settings file in place and check that
the machine can actually do the job.

Run it from an unzipped release. It is safe to run again -- it skips whatever is
already done, so it doubles as the way to pick up a new release's dependencies.

  .\setup_render_machine.ps1            # set up, then report
  .\setup_render_machine.ps1 -Force     # rebuild the virtual environment first
#>
[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppRoot = Resolve-Path (Join-Path $ScriptDir '..\..')
Set-Location $AppRoot

$VenvDir = Join-Path $AppRoot '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$Requirements = Join-Path $ScriptDir 'requirements.txt'
$EnvFile = Join-Path $ScriptDir 'renderer.env'
$EnvExample = Join-Path $ScriptDir 'renderer.env.example'

function Say([string]$Message) { Write-Host "  $Message" }

Write-Host ""
Write-Host "Pwllheli render machine setup"
Write-Host "  folder $AppRoot"
Write-Host ""

# --- 1. Python -------------------------------------------------------------
if ($Force -and (Test-Path $VenvDir)) {
    Say "removing the existing virtual environment (-Force)"
    Remove-Item -Recurse -Force $VenvDir
}
if (-not (Test-Path $VenvPython)) {
    Say "creating the virtual environment"
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { & py -3 -m venv $VenvDir } else { & python -m venv $VenvDir }
    if (-not (Test-Path $VenvPython)) {
        throw "The virtual environment was not created. Install Python 3.11+ and tick 'Add to PATH'."
    }
} else {
    Say "virtual environment already present"
}

# --- 2. The renderer's packages -------------------------------------------
# Not the app's requirements.txt: this machine runs no web server and opens no
# database. pip writes progress to stderr, so its exit code is the only signal
# worth reading -- the same trap start_race_officer.ps1 documents at length.
Say "installing the render side's packages"
& $VenvPython -m pip install --disable-pip-version-check --quiet -r $Requirements
if ($LASTEXITCODE -ne 0) {
    throw "pip failed (exit $LASTEXITCODE). Run it again without --quiet to see why."
}

# --- 3. Settings -----------------------------------------------------------
if (-not (Test-Path $EnvFile)) {
    Copy-Item $EnvExample $EnvFile
    Say "created renderer.env from the example -- it still needs your settings"
    $NeedsSettings = $true
} else {
    Say "renderer.env already present"
    $NeedsSettings = $false
}

# --- 4. Does this machine have what it needs? ------------------------------
Write-Host ""
& (Join-Path $ScriptDir 'run_renderer.ps1') -Check
$checkFailed = $LASTEXITCODE -ne 0

Write-Host ""
if ($NeedsSettings -or $checkFailed) {
    Write-Host "Not ready yet. What is left:"
    if ($NeedsSettings) {
        Write-Host "  1. Put the club's settings in:"
        Write-Host "       $EnvFile"
        Write-Host "     The five R2 values come from the app's Settings -> Video page,"
        Write-Host "     and they must match it exactly or this machine will sit here"
        Write-Host "     seeing no work for ever."
    }
    if ($checkFailed) {
        Write-Host "  2. Fix whatever the check above listed, then run this again."
    }
    Write-Host ""
    Write-Host "  Then:  .\deploy\render_machine\install_render_task.ps1"
    exit 1
}

Write-Host "Ready. To have it start with Windows and stay running:"
Write-Host "    .\deploy\render_machine\install_render_task.ps1"
Write-Host ""
Write-Host "Or to watch one job go through by hand first:"
Write-Host "    .\deploy\render_machine\run_renderer.ps1 -Once"
exit 0
