@echo off
REM Sync Gmail from the command line (the app's Sync button does the same).
cd /d "%~dp0"
venv\Scripts\python.exe -m jobtracker sync %*
pause
