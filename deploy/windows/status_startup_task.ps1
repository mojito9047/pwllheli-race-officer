<# Copyright © 2026 CapeNet Ltd. All Rights Reserved. #>
[CmdletBinding()]
param(
    [string]$TaskName = 'Pwllheli Race Officer',
    [int]$Port = 5050
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppRoot = Resolve-Path (Join-Path $ScriptDir '..\..')
$LogPath = Join-Path $AppRoot 'runtime\logs\race_officer.log'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($task) {
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "Task:        $TaskName"
    Write-Host "State:       $($task.State)"
    Write-Host "Last run:    $($info.LastRunTime)"
    Write-Host "Last result: $($info.LastTaskResult)"
} else {
    Write-Host "Task '$TaskName' is not installed."
}

Write-Host "Admin URL:   http://localhost:$Port/admin"
Write-Host "Public URL:  http://localhost:$Port"
Write-Host "Log file:    $LogPath"

try {
    $response = Invoke-WebRequest -Uri "http://localhost:$Port/admin" -UseBasicParsing -TimeoutSec 3
    Write-Host "HTTP check:  OK ($($response.StatusCode))"
} catch {
    Write-Host "HTTP check:  Not responding on port $Port"
}

if (Test-Path $LogPath) {
    Write-Host ""
    Write-Host "Last 20 log lines:"
    Get-Content $LogPath -Tail 20
}
