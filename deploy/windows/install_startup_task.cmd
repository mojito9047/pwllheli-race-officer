@echo off
REM Copyright © 2026 CapeNet Ltd. MIT licence; see LICENSE.
REM Install or update the Windows startup scheduled task.
set SCRIPT_DIR=%~dp0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%install_startup_task.ps1" %*
