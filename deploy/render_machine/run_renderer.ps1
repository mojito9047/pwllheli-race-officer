<#
Copyright © 2026 CapeNet Ltd. All Rights Reserved.

Runs the 3D race-replay renderer on a Windows render machine.

Reads renderer.env from beside this script (KEY=value, # comments), then
watches the bucket for jobs. Ctrl-C stops it.

  .\run_renderer.ps1            # the loop; leave it running
  .\run_renderer.ps1 -Once      # take one job, or nothing, and stop
  .\run_renderer.ps1 -Check     # say what is configured and exit
#>
[CmdletBinding()]
param(
    [switch]$Once,
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppRoot = Resolve-Path (Join-Path $ScriptDir '..\..')
Set-Location $AppRoot

$EnvFile = Join-Path $ScriptDir 'renderer.env'
if (-not (Test-Path $EnvFile)) {
    throw "No renderer.env. Copy renderer.env.example to renderer.env and fill it in."
}

# Values are taken verbatim after the first '=': an R2 secret can contain
# anything, and trimming or splitting again would quietly corrupt it.
foreach ($line in Get-Content $EnvFile) {
    $text = $line.Trim()
    if ($text -eq '' -or $text.StartsWith('#')) { continue }
    $split = $text.IndexOf('=')
    if ($split -lt 1) { continue }
    $name = $text.Substring(0, $split).Trim()
    $value = $text.Substring($split + 1)
    if ($value -ne '') { Set-Item -Path "env:$name" -Value $value }
}

$VenvPython = Join-Path $AppRoot '.venv\Scripts\python.exe'
$Python = if (Test-Path $VenvPython) { $VenvPython } else { 'python' }

if ($Check) {
    # Reports everything rather than stopping at the first thing missing: the
    # point of a check is one list of what to fix, not three round trips.
    $problems = @()
    Write-Host "root     $AppRoot"
    Write-Host "python   $Python"
    foreach ($name in 'R2_ACCOUNT_ID', 'R2_BUCKET', 'R2_PUBLIC_BASE_URL', 'RENDER_WORKERS', 'RENDER_FRAME_RANGE') {
        Write-Host ("{0,-20} {1}" -f $name, [Environment]::GetEnvironmentVariable($name))
    }
    # Never the value, only whether there is one. This output gets pasted.
    foreach ($name in 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'MAPBOX_TOKEN') {
        $value = [Environment]::GetEnvironmentVariable($name)
        Write-Host ("{0,-20} {1}" -f $name, $(if ($value) { 'set' } else { 'MISSING' }))
        if (-not $value) { $problems += "$name is not set in renderer.env" }
    }

    # One python call that reports on every package, rather than one call per
    # package with stderr redirected: on PowerShell 5.1 redirecting a native
    # command's stderr turns its output into an error record, and with
    # ErrorActionPreference 'Stop' the check dies on the first missing module
    # instead of listing them. find_spec also avoids importing anything.
    # One line, and no double quotes inside it: PowerShell 5.1 strips those
    # when it hands an argument to a native executable, which turned a tidy
    # multi-line probe into a SyntaxError from python.
    $probe = "import importlib.util as u; print(chr(10).join(n.ljust(20) + ('ok' if u.find_spec(n) else 'MISSING') for n in ['PIL','numpy','av','tifffile','imagecodecs','fontTools','brotli','werkzeug']))"
    foreach ($line in (& $Python -c $probe)) {
        Write-Host $line
        if ($line -match 'MISSING') { $problems += "$($line.Split()[0]) is not installed for $Python" }
    }

    # Asks the renderer's own resolver rather than repeating it here. A
    # second copy of this logic is how you end up with a check that passes and
    # a render that dies on CreateProcess: existing on disk and being a
    # runnable Blender are not the same thing, and only the resolver knows the
    # difference.
    $ask = "import sys; sys.path.insert(0, 'scripts/replay3d'); import render_parallel as rp; p = rp.blender_path(); print(p if p else rp.blender_not_found()); sys.exit(0 if p else 1)"
    $found = & $Python -c $ask
    if ($LASTEXITCODE -eq 0) {
        Write-Host ("{0,-20} {1}" -f 'blender', $found)
    } else {
        Write-Host ("{0,-20} NOT FOUND" -f 'blender')
        $found | ForEach-Object { Write-Host "  $_" }
        $problems += 'no working Blender'
    }

    if ($problems.Count -eq 0) {
        Write-Host "`nReady."
        exit 0
    }
    Write-Host "`nNot ready:"
    $problems | ForEach-Object { Write-Host "  - $_" }
    exit 1
}

$cliArgs = @('scripts\replay3d\renderer.py')
if ($Once) { $cliArgs += '--once' }
& $Python @cliArgs
exit $LASTEXITCODE
