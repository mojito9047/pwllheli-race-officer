<#
Copyright © 2026 CapeNet Ltd. All Rights Reserved.

What the renderer is doing: the scheduled task, and what the club's bucket says
about it. The bucket is the honest half -- the task can be running happily while
the renderer is pointed at the wrong place and seeing no work.
#>
[CmdletBinding()]
param([string]$TaskName = 'Pwllheli 3D Replay Renderer')

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppRoot = Resolve-Path (Join-Path $ScriptDir '..\..')
Set-Location $AppRoot

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "No scheduled task named '$TaskName'."
    Write-Host "Install it with:  .\deploy\render_machine\install_render_task.ps1"
    exit 1
}
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "task        $TaskName"
Write-Host "state       $($task.State)"
# Windows dates a task that has never run to 1899 or 1932 depending on the
# build, which reads as a real date somebody then tries to account for.
$lastRun = if ($info.LastRunTime -and $info.LastRunTime.Year -gt 2000) {
    "$($info.LastRunTime)  (result $($info.LastTaskResult))"
} else { 'never' }
Write-Host "last run    $lastRun"
Write-Host "next run    $($info.NextRunTime)"

$VenvPython = Join-Path $AppRoot '.venv\Scripts\python.exe'
$Python = if (Test-Path $VenvPython) { $VenvPython } else { 'python' }
$EnvFile = Join-Path $ScriptDir 'renderer.env'
if (Test-Path $EnvFile) {
    foreach ($line in Get-Content $EnvFile) {
        $text = $line.Trim()
        if ($text -eq '' -or $text.StartsWith('#')) { continue }
        $split = $text.IndexOf('=')
        if ($split -lt 1) { continue }
        $value = $text.Substring($split + 1)
        if ($value -ne '') { Set-Item -Path "env:$($text.Substring(0, $split).Trim())" -Value $value }
    }
}
Write-Host ""
Write-Host "what the bucket says:"
# The reporting lives in render_status.py beside this script. Passing python
# source through PowerShell to a native command loses the quotes -- the first
# attempt died with "'(' was never closed" -- and a file is testable on its own.
& $Python (Join-Path $ScriptDir 'render_status.py')
