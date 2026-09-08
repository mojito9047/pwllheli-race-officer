<#
Copyright © 2026 CapeNet Ltd. All Rights Reserved.

Start script for the Pwllheli Race Officer app on a Windows race-hut PC.
It is designed to be called directly by Task Scheduler, or via start_race_officer.cmd for manual diagnostics.
#>
[CmdletBinding()]
param(
    [switch]$SetupOnly
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppRoot = Resolve-Path (Join-Path $ScriptDir '..\..')
Set-Location $AppRoot

$RuntimeDir = Join-Path $AppRoot 'runtime'
$CacheDir = Join-Path $RuntimeDir 'cache'
$LogDir = Join-Path $RuntimeDir 'logs'
$VenvDir = Join-Path $AppRoot '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$Requirements = Join-Path $AppRoot 'requirements.txt'
$LogPath = Join-Path $LogDir 'race_officer.log'
$RequirementsStamp = Join-Path $CacheDir 'requirements.sha256'

New-Item -ItemType Directory -Force -Path $RuntimeDir, $CacheDir, $LogDir | Out-Null

# One writer for the log, with the encoding stated rather than inherited.
#
# The log used to be written by two different cmdlets: Add-Content for the
# timestamped lines and Tee-Object for the piped output of pip and the app. They do
# not agree on a default encoding, and on the hut's build of PowerShell 5.1
# Tee-Object writes UTF-16LE while Add-Content writes single-byte text — so a
# v0.243 install log came out half readable and half "R e q u i r e m e n t   a l r
# e a d y   s a t i s f i e d". (Windows 11's newer 5.1 build writes UTF-8 from
# Tee-Object, which is why it looked fine in development.)
#
# UTF-8 without a BOM, appended directly, so the file is the same on every machine
# and can be pasted anywhere.
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Append-LogLine {
    param([string]$Text)
    try {
        [System.IO.File]::AppendAllText($LogPath, $Text + "`r`n", $Utf8NoBom)
    } catch {
        # A log we cannot write must not stop the app from starting.
    }
}

function Write-LogLine {
    param([string]$Message)
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$stamp] $Message"
    Write-Host $line
    Append-LogLine $line
}

function New-VirtualEnvironment {
    if (Test-Path $VenvPython) {
        return $false
    }

    Write-LogLine "Creating Python virtual environment at $VenvDir"
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        & py -3 -m venv $VenvDir
    } else {
        $python = Get-Command python -ErrorAction Stop
        & $python.Source -m venv $VenvDir
    }

    if (-not (Test-Path $VenvPython)) {
        throw "Virtual environment was not created. Check that Python 3.10+ is installed and on PATH."
    }
    return $true
}

function Invoke-LoggedCmdLine {
    param(
        [Parameter(Mandatory=$true)]
        [string]$CommandLine,
        [string]$Description = 'command'
    )

    # Exit code is the only trustworthy signal for a native command, so check that
    # and let stderr through as ordinary text. Same reasoning, and the same shape,
    # as Invoke-Native in release.ps1.
    #
    # Routing through cmd.exe was meant to achieve this and did not: the 2>&1 below
    # is applied by PowerShell to *cmd.exe's* stderr, and cmd passes the child's
    # stderr straight through to it, so PowerShell still saw a native command
    # writing to stderr and still raised NativeCommandError under Stop. One line of
    # pip output — "WARNING: Ignoring invalid distribution ~ip", left by pip
    # upgrading itself — was enough to abort startup before the app was ever
    # launched (v0.238). Relaxing the preference only here keeps Stop semantics for
    # the rest of the script.
    #
    # Do not "simplify" this by moving the redirection into the cmd.exe command
    # line: PowerShell 5.1 re-quotes native arguments and mangles the nested quotes
    # around the interpreter path.
    #
    # "$_" flattens each record to its message text, without which a stderr line
    # reaches the log wrapped in six lines of ErrorRecord scaffolding ("At line:30
    # char:9", CategoryInfo, ...) — in the log the hut is troubleshot from.
    #
    # Written through Append-LogLine rather than Tee-Object so the whole file has
    # one encoding; see the note on that function.
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & cmd.exe /d /s /c "`"$CommandLine`"" 2>&1 |
            ForEach-Object {
                $line = "$_"
                Write-Host $line
                Append-LogLine $line
            }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "$Description failed with exit code $exitCode"
    }
}

function Install-Requirements {
    if (-not (Test-Path $Requirements)) {
        throw "requirements.txt not found at $Requirements"
    }
    Write-LogLine "Installing/updating Python requirements"
    $quotedPython = '"' + $VenvPython + '"'
    Invoke-LoggedCmdLine "$quotedPython -m pip install --upgrade pip --no-cache-dir" "pip upgrade"
    Invoke-LoggedCmdLine "$quotedPython -m pip install --no-cache-dir -r `"$Requirements`"" "requirements install"
}

$venvCreated = New-VirtualEnvironment
$requirementsHash = (Get-FileHash -Path $Requirements -Algorithm SHA256).Hash
$previousHash = if (Test-Path $RequirementsStamp) { (Get-Content $RequirementsStamp -ErrorAction SilentlyContinue | Select-Object -First 1) } else { '' }
if ($venvCreated -or ($requirementsHash -ne $previousHash)) {
    Install-Requirements
    Set-Content -Path $RequirementsStamp -Value $requirementsHash
} else {
    Write-LogLine "Python requirements unchanged. Skipping pip install."
}

if ($SetupOnly) {
    Write-LogLine "Setup complete. Not starting the app because -SetupOnly was supplied."
    exit 0
}

if (-not $env:RO_HOST) { $env:RO_HOST = '0.0.0.0' }
if (-not $env:RO_PORT) { $env:RO_PORT = '5050' }
if (-not $env:RO_THREADS) { $env:RO_THREADS = '8' }

Write-LogLine "Starting Pwllheli Race Officer from $AppRoot"
Write-LogLine "URL on this PC: http://localhost:$($env:RO_PORT)/admin"
Write-LogLine "Public page on this PC: http://localhost:$($env:RO_PORT)"
Write-LogLine "Press Ctrl+C to stop if running interactively."

$AppPy = Join-Path $AppRoot 'app.py'
$quotedPython = '"' + $VenvPython + '"'
Invoke-LoggedCmdLine "$quotedPython `"$AppPy`"" "Pwllheli Race Officer"
$exitCode = $LASTEXITCODE
Write-LogLine "Pwllheli Race Officer stopped with exit code $exitCode"
exit $exitCode
