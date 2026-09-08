<#
Copyright © 2026 CapeNet Ltd. All Rights Reserved.

Read-only health check for the race-office PC.

Written after the app started refusing connections mid-race and there were four
plausible causes in the code, none of them measured. This answers the questions
that would have settled it in a minute: is the machine throttling, how many
connections are actually open, is anything genuinely slow, and are the Windows
settings that quietly cost this app in the state we think.

It CHANGES NOTHING. Every call is a query. Safe to run during a race, though a
quiet moment is kinder.

    powershell -ExecutionPolicy Bypass -File deploy\windows\check_hut_pc.ps1

Some checks (Defender exclusions, a couple of power settings) need an elevated
prompt; without one they say so and the rest still runs.

Nothing it prints is a secret: settings values are reported by name only, and
anything that looks like a token, secret or password is shown as <set> with its
length. Output is meant to be pasted somewhere for a second opinion.
#>
[CmdletBinding()]
param(
    [int]$Port = 5050,
    [int]$SlowLogLines = 2000
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppRoot = (Resolve-Path (Join-Path $ScriptDir '..\..')).Path

function Section($title) {
    Write-Output ""
    Write-Output ("=" * 72)
    Write-Output "  $title"
    Write-Output ("=" * 72)
}
function Item($label, $value) {
    Write-Output ("  {0,-34} {1}" -f $label, $value)
}
function Note($text) { Write-Output "    -> $text" }

$IsAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Output ""
Write-Output "Pwllheli Race Officer - race-office PC check"
Write-Output ("  {0}   app root: {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $AppRoot)
Write-Output ("  elevated: {0}" -f $(if ($IsAdmin) { 'yes' } else { 'NO - some checks will be skipped' }))

# ---------------------------------------------------------------- machine ---
Section "Machine"
try {
    $cs = Get-CimInstance Win32_ComputerSystem
    $os = Get-CimInstance Win32_OperatingSystem
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    Item "Model" ("{0} {1}" -f $cs.Manufacturer, $cs.Model)
    Item "CPU" $cpu.Name.Trim()
    Item "Cores / threads" ("{0} / {1}" -f $cpu.NumberOfCores, $cpu.NumberOfLogicalProcessors)
    Item "RAM" ("{0:N1} GB total, {1:N1} GB free" -f `
        ($os.TotalVisibleMemorySize / 1MB), ($os.FreePhysicalMemory / 1MB))
    Item "Windows" ("{0} ({1})" -f $os.Caption, $os.Version)
    $up = (Get-Date) - $os.LastBootUpTime
    Item "Uptime" ("{0:N0}d {1}h {2}m" -f $up.Days, $up.Hours, $up.Minutes)
} catch { Note "could not read machine details: $($_.Exception.Message)" }

# ------------------------------------------------------------- throttling ---
Section "CPU speed and throttling"
Write-Output "  Turbo is a burst, not a sustained state. On a fanless mini PC - which the"
Write-Output "  hut's MINIX NEO Z350-0dB is - what matters is the speed it settles at"
Write-Output "  under load, not the figure on the box."
try {
    $maxMhz = (Get-CimInstance Win32_Processor | Select-Object -First 1).MaxClockSpeed
    Item "Rated base/max clock" ("{0} MHz" -f $maxMhz)
    $samples = @()
    foreach ($i in 1..5) {
        $pct = (Get-Counter '\Processor Information(_Total)\% of Maximum Frequency' `
                -ErrorAction Stop).CounterSamples[0].CookedValue
        $load = (Get-Counter '\Processor Information(_Total)\% Processor Time' `
                -ErrorAction Stop).CounterSamples[0].CookedValue
        $samples += [pscustomobject]@{ Pct = $pct; Load = $load }
        Start-Sleep -Milliseconds 400
    }
    $avgPct = ($samples | Measure-Object Pct -Average).Average
    $avgLoad = ($samples | Measure-Object Load -Average).Average
    Item "Running at" ("{0:N0}% of maximum frequency" -f $avgPct)
    Item "CPU load" ("{0:N0}%" -f $avgLoad)
    if ($avgPct -lt 70 -and $avgLoad -gt 50) {
        Note "BUSY AND SLOWED - looks like thermal or power throttling. In a fanless"
        Note "case the usual cause is something re-encoding video continuously."
    } elseif ($avgPct -lt 70) {
        Note "below maximum, but the CPU is idle - normal power saving, not throttling."
    } else {
        Note "no sign of throttling right now."
    }
} catch { Note "frequency counters unavailable: $($_.Exception.Message)" }

try {
    $t = Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop |
         Select-Object -First 1
    Item "Thermal zone" ("{0:N1} C" -f (($t.CurrentTemperature / 10) - 273.15))
} catch { Item "Thermal zone" "not exposed by this BIOS (common on mini PCs)" }

# ------------------------------------------------------------------ disks ---
Section "Storage"
try {
    Get-Volume | Where-Object { $_.DriveLetter -and $_.Size -gt 0 } | ForEach-Object {
        $freePct = 100 * $_.SizeRemaining / $_.Size
        Item ("Drive {0}:" -f $_.DriveLetter) `
            ("{0:N0} GB free of {1:N0} GB ({2:N0}%)  [{3}]" -f `
                ($_.SizeRemaining / 1GB), ($_.Size / 1GB), $freePct, $_.FileSystemType)
        if ($freePct -lt 15) { Note "LOW - NVMe slows markedly when nearly full." }
    }
} catch { Note "could not enumerate volumes: $($_.Exception.Message)" }

foreach ($rel in @('data\video_clips', 'runtime\video', 'runtime\logs')) {
    $path = Join-Path $AppRoot $rel
    if (Test-Path $path) {
        $sz = (Get-ChildItem $path -Recurse -File -ErrorAction SilentlyContinue |
               Measure-Object Length -Sum).Sum
        Item $rel ("{0:N2} GB" -f ($sz / 1GB))
    }
}
foreach ($db in @('data\race_officer.db', 'data\track_positions.db', 'data\power_history.db')) {
    $path = Join-Path $AppRoot $db
    if (Test-Path $path) {
        Item $db ("{0:N1} MB" -f ((Get-Item $path).Length / 1MB))
    }
}

# -------------------------------------------------------------- the app ----
Section "The app"
$appProcs = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
              Where-Object { $_.CommandLine -like '*app.py*' })
if (-not $appProcs) {
    Note "no python process running app.py was found."
} else {
    foreach ($p in $appProcs) {
        $proc = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        Item ("PID {0}" -f $p.ProcessId) ("started {0}, {1} threads, {2:N0} MB, {3} handles" -f `
            $proc.StartTime, $proc.Threads.Count, ($proc.WorkingSet64 / 1MB), $proc.HandleCount)
    }
    # A normal launch is TWO processes: a small launcher stub and the server that
    # actually holds the port and the thread pool. Warning on any pair called a
    # healthy hut PC broken, which is worse than saying nothing. A real duplicate
    # is two *servers*, or launches at different times.
    $servers = @($appProcs | ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue } |
                 Where-Object { $_ -and $_.Threads.Count -gt 4 })
    $starts = @($appProcs | ForEach-Object { (Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue).StartTime } |
                Where-Object { $_ } | ForEach-Object { $_.ToString('yyyy-MM-dd HH:mm:ss') } | Sort-Object -Unique)
    if ($servers.Count -gt 1 -or $starts.Count -gt 1) {
        Note "more than one app.py launch is running ($($starts.Count) start time(s),"
        Note "$($servers.Count) with a thread pool). Only one can hold the port; the"
        Note "others are idle and will confuse a restart. Worth tidying up."
    } else {
        Note "one launch (a launcher stub plus the server) - normal."
    }
}

# --------------------------------------------------------- connections -----
Section "Connections on port $Port"
Write-Output "  The connection limit counts OPEN SOCKETS, not people. A browser holds"
Write-Output "  up to six per origin, so the old default of 100 was about sixteen viewers."
try {
    # SilentlyContinue, not Stop: Get-NetTCPConnection raises "no matching objects"
    # when nothing is listening rather than returning an empty set, and an app that
    # is down is precisely when somebody runs this. That case has to read as an
    # answer, not as a CIM error the reader has to decode.
    $listen = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if (-not $listen) {
        Note "NOTHING IS LISTENING on port $Port - the app is not serving."
    }
    $conns = @(Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
               Where-Object { $_.State -ne 'Listen' })
    Item "Open connections" $conns.Count
    $conns | Group-Object State | Sort-Object Count -Descending | ForEach-Object {
        Item ("  {0}" -f $_.Name) $_.Count
    }
    $peers = $conns | Group-Object RemoteAddress | Sort-Object Count -Descending |
             Select-Object -First 5
    if ($peers) {
        Write-Output "  Busiest peers (a tunnel or proxy will dominate this legitimately):"
        $peers | ForEach-Object { Item ("  {0}" -f $_.Name) ("{0} connection(s)" -f $_.Count) }
    }
} catch { Note "could not read TCP connections: $($_.Exception.Message)" }

# ------------------------------------------------------------- slow log ----
Section "Slow requests (runtime/logs/slow.log)"
$slow = Join-Path $AppRoot 'runtime\logs\slow.log'
if (-not (Test-Path $slow)) {
    Note "no slow.log yet. It appears once a request exceeds RO_SLOW_REQUEST_MS"
    Note "(default 250 ms). No file after a busy race is itself a useful answer:"
    Note "it means nothing was slow, and the limit was sockets rather than speed."
} else {
    $lines = Get-Content $slow -Tail $SlowLogLines
    Item "Lines examined" ("{0} (last {1})" -f $lines.Count, $SlowLogLines)
    $parsed = foreach ($l in $lines) {
        if ($l -match '^\s*(\S+ \S+)\s*\|\s*(\d+) ms \|\s*(\S+)\s+(\S+)\s*\|\s*(\d+)') {
            [pscustomobject]@{ When = $matches[1]; Ms = [int]$matches[2]
                               Method = $matches[3]; Path = $matches[4]; Status = $matches[5] }
        }
    }
    if (-not $parsed) { Note "no lines matched the expected format." }
    else {
        Write-Output "  Worst offenders by total time held:"
        $parsed | Group-Object Path | ForEach-Object {
            [pscustomobject]@{
                Path  = $_.Name
                Count = $_.Count
                TotalS = [math]::Round((($_.Group | Measure-Object Ms -Sum).Sum) / 1000, 1)
                WorstMs = ($_.Group | Measure-Object Ms -Maximum).Maximum
            }
        } | Sort-Object TotalS -Descending | Select-Object -First 8 | ForEach-Object {
            Item $_.Path ("{0} slow request(s), {1}s total, worst {2} ms" -f `
                $_.Count, $_.TotalS, $_.WorstMs)
        }
    }
}

# ----------------------------------------------------------- power plan ----
Section "Power and sleep"
try {
    $scheme = (powercfg /getactivescheme) -join ''
    Item "Active plan" $scheme.Trim()
} catch { Note "powercfg unavailable." }
try {
    $sleepy = (powercfg /a) -join "`n"
    if ($sleepy -match 'The following sleep states are available') {
        Note "sleep states are available - confirm the PC is set never to sleep."
    }
} catch { }
foreach ($q in @(
    @{ Label = 'USB selective suspend'; Sub = '2a737441-1930-4402-8d77-b2bebba308a3'; Set = '48e6b7a6-50f5-4782-a5d4-53bb8f07e226' }
)) {
    try {
        $out = (powercfg /q SCHEME_CURRENT $q.Sub $q.Set) -join "`n"
        $ac = [regex]::Match($out, 'Current AC Power Setting Index:\s*(0x[0-9a-f]+)').Groups[1].Value
        $state = if ($ac -eq '0x00000000') { 'Disabled (good)' } else { "Enabled ($ac)" }
        Item $q.Label $state
        if ($ac -ne '0x00000000') {
            Note "if the horn is on a USB serial adapter, Windows suspending that port"
            Note "is a classic cause of a horn that does not fire."
        }
    } catch { Item $q.Label "could not read" }
}

# -------------------------------------------------------------- Defender ---
Section "Windows Defender"
Write-Output "  FFmpeg writes video segments continuously and SQLite fsyncs constantly."
Write-Output "  Real-time scanning inspects every one of those writes."
try {
    $st = Get-MpComputerStatus -ErrorAction Stop
    Item "Real-time protection" $st.RealTimeProtectionEnabled
    $pref = Get-MpPreference -ErrorAction Stop
    $ex = @($pref.ExclusionPath)
    # Unelevated, Get-MpPreference does not fail - it returns the string
    # "N/A: Must be an administrator to view exclusions". Treating that as a list
    # of paths makes the check below announce that nothing is excluded, which is a
    # statement it has no business making. Not knowing is not the same as none.
    $readable = ($ex.Count -eq 0) -or -not ($ex | Where-Object { "$_" -like 'N/A*' })
    if (-not $readable) {
        Item "Exclusion paths" "cannot be read without an elevated prompt"
        Note "re-run this in an elevated PowerShell to check the exclusions."
    }
    elseif ($ex.Count -eq 0) { Item "Exclusion paths" "none" }
    else { $ex | ForEach-Object { Item "Excluded" $_ } }

    $want = @('data\video_clips', 'runtime\video', 'data\race_officer.db', 'data\track_positions.db')
    $missing = @()
    if ($readable) {
        foreach ($w in $want) {
            $full = Join-Path $AppRoot $w
            if (-not ($ex | Where-Object { $full -like "$_*" })) { $missing += $w }
        }
    }
    if ($missing) {
        Note "not excluded: $($missing -join ', ')"
        Note "Excluding these DATA paths is the biggest Windows-side saving available."
        Note "Exclude data directories only - never the app code or the Python install,"
        Note "which is where an executable could actually land. This box is reachable"
        Note "through a Cloudflare tunnel, so that distinction matters."
    }
} catch {
    if (-not $IsAdmin) { Note "needs an elevated prompt to read Defender settings." }
    else { Note "could not read Defender settings: $($_.Exception.Message)" }
}

# ------------------------------------------------- update / bandwidth ------
Section "Windows Update bandwidth"
try {
    $do = Get-DeliveryOptimizationStatus -ErrorAction Stop | Select-Object -First 1
    if ($do) { Item "Delivery Optimization" "active - peer sharing can eat the 4G uplink" }
    else { Item "Delivery Optimization" "idle" }
} catch { Item "Delivery Optimization" "not reporting (fine)" }

Write-Output ""
Write-Output ("=" * 72)
Write-Output "  Nothing above was changed. Paste this output somewhere useful."
Write-Output ("=" * 72)
Write-Output ""

# powercfg and friends leave their own exit codes behind, and a non-zero one from
# a query nobody failed reads as "the check errored". This script reports; it does
# not pass or fail.
exit 0
