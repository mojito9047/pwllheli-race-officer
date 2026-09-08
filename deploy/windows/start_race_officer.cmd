@echo off
REM Copyright © 2026 CapeNet Ltd. All Rights Reserved.
REM Start Pwllheli Race Officer using the project PowerShell startup script.
set SCRIPT_DIR=%~dp0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start_race_officer.ps1" %*
