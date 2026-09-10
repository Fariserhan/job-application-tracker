@echo off
REM ============================================================
REM  Job Application Suite - desktop launcher
REM  Starts the Streamlit dashboard (if it isn't already running)
REM  and opens it in your default browser.
REM ============================================================
title Job Application Suite
cd /d "%~dp0"

REM Already running on port 8501? Just open the browser.
powershell -NoProfile -Command "try { (New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',8501); exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
    start "" http://localhost:8501
    exit /b
)

REM Start the server in a minimised window; Streamlit opens the browser when ready.
start "Job Application Suite (server)" /min "venv\Scripts\streamlit.exe" run dashboard.py --server.port 8501 --server.headless=false
exit /b
