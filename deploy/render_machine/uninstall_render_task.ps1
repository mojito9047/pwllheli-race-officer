<#
Copyright © 2026 CapeNet Ltd. All Rights Reserved.

Stop the renderer and remove its scheduled task. Nothing else is touched: the
virtual environment, the settings and the cached map tiles all stay, so
reinstalling is one command and costs no downloads.
#>
[CmdletBinding()]
param([string]$TaskName = 'Pwllheli 3D Replay Renderer')

$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "No scheduled task named '$TaskName'; nothing to remove."
    exit 0
}
if ($task.State -eq 'Running') {
    Write-Host "Stopping '$TaskName'"
    Stop-ScheduledTask -TaskName $TaskName
}
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed '$TaskName'."
Write-Host "A job already claimed by this machine will go stale after a few minutes"
Write-Host "and another renderer, or this one when reinstalled, can pick it up."
