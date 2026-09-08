<# Copyright © 2026 CapeNet Ltd. All Rights Reserved. #>
[CmdletBinding()]
param(
    [string]$TaskName = 'Pwllheli Race Officer'
)

$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    throw "Scheduled task '$TaskName' is not installed. Run install_startup_task.cmd first."
}

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName $TaskName
Write-Host "Restarted scheduled task '$TaskName'."
