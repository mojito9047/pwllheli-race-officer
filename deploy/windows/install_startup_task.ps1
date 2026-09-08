<#
Copyright © 2026 CapeNet Ltd. All Rights Reserved.

Install a Windows Scheduled Task that starts Pwllheli Race Officer when the
current race-office user logs in.  This is preferred to a Windows service for
this app because horn, audio and camera devices normally behave more reliably
inside the signed-in race-officer session.
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'Pwllheli Race Officer',
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StartPs1 = Join-Path $ScriptDir 'start_race_officer.ps1'
$AppRoot = Resolve-Path (Join-Path $ScriptDir '..\..')

if (-not (Test-Path $StartPs1)) {
    throw "Start script not found: $StartPs1"
}

Write-Host "Preparing Python environment..."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StartPs1 -SetupOnly

$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
# Run PowerShell directly with -WindowStyle Hidden.  Using cmd.exe here leaves
# a visible command window on the race-office desktop for as long as the app runs.
$actionArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartPs1`""
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $actionArgs -WorkingDirectory $AppRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 30)
# PowerShell's ScheduledTask RunLevel enum values are Limited or Highest.
# Limited keeps the task in the normal signed-in user context, which is what
# the app needs for desktop audio, USB/serial horn hardware and cameras.
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$description = 'Starts the Pwllheli Race Officer Waitress app when the race-office user logs in.'

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Updating existing scheduled task '$TaskName'"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
} else {
    Write-Host "Creating scheduled task '$TaskName'"
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description $description | Out-Null

if (-not $NoStart) {
    Write-Host "Starting scheduled task '$TaskName'"
    Start-ScheduledTask -TaskName $TaskName
}

Write-Host "Installed. Admin page: http://localhost:5050/admin"
Write-Host "Public page: http://localhost:5050"
Write-Host "Log file: $AppRoot\runtime\logs\race_officer.log"
