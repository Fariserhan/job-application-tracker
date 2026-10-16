@echo off
REM Background Gmail sync: double-click to turn it on, or run "auto_sync.bat off".
cd /d "%~dp0"
if /i "%~1"=="off" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\auto_sync.ps1" -Off
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\auto_sync.ps1"
)
pause
