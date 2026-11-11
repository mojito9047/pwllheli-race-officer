@echo off
REM Copyright © 2026 CapeNet Ltd. MIT licence; see LICENSE.
set SCRIPT_DIR=%~dp0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%setup_render_machine.ps1" %*
