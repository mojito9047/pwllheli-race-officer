<#
Copyright © 2026 CapeNet Ltd. All Rights Reserved.

Install a Windows Scheduled Task that runs the 3D replay renderer whenever this
machine is logged in, so a race queued from the hut is picked up without anybody
going to the render machine first.

At logon, as the signed-in user, deliberately. The renderer drives Blender and
EEVEE is a realtime engine: it wants a real graphics driver in a real desktop
session. A task set to "run whether logged on or not" gets a session with no
GPU, and every job fails at the first frame -- the same reason the hut's own
task runs in the signed-in session for its audio and serial hardware.
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'Pwllheli 3D Replay Renderer',
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunPs1 = Join-Path $ScriptDir 'run_renderer.ps1'
$AppRoot = Resolve-Path (Join-Path $ScriptDir '..\..')

if (-not (Test-Path $RunPs1)) { throw "Runner not found: $RunPs1" }
if (-not (Test-Path (Join-Path $ScriptDir 'renderer.env'))) {
    throw "No renderer.env. Run setup_render_machine.ps1 first and fill in the settings."
}

Write-Host "Checking this machine can render before installing the task..."
& $RunPs1 -Check
if ($LASTEXITCODE -ne 0) {
    throw "Not ready -- see above. Installing a task that cannot render only hides the problem."
}

$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$actionArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$RunPs1`""
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $actionArgs -WorkingDirectory $AppRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Days 30)
# IgnoreNew matters more here than on the hut: two renderers watching one bucket
# take each other's jobs and overwrite each other's heartbeat, and the dashboard
# then names whichever wrote last.
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$description = 'Watches the club bucket for 3D replay jobs and renders them. Needs a signed-in session for the graphics card.'

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Updating existing scheduled task '$TaskName'"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
} else {
    Write-Host "Creating scheduled task '$TaskName'"
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Description $description | Out-Null

if (-not $NoStart) {
    Write-Host "Starting scheduled task '$TaskName'"
    Start-ScheduledTask -TaskName $TaskName
}

Write-Host ""
Write-Host "Installed. It is watching the club's bucket now and after every logon."
Write-Host "  what it is doing : .\deploy\render_machine\status_render_task.ps1"
Write-Host "  stop it          : .\deploy\render_machine\uninstall_render_task.ps1"
Write-Host ""
Write-Host "The app's dashboard card is the place to watch from: it says whether a"
Write-Host "render machine has checked in at all, which is what a queued job needs."
