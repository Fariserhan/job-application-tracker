@echo off
REM ============================================================
REM  Job Search Analytics - desktop launcher (Windows)
REM  Starts the Streamlit app if it isn't running, then opens it.
REM ============================================================
title Job Search Analytics
cd /d "%~dp0"

powershell -NoProfile -Command "try { (New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',8501); exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
    start "" http://localhost:8501
    exit /b
)
start "Job Search Analytics (server)" /min "venv\Scripts\python.exe" -m streamlit run app\streamlit_app.py --server.port 8501 --server.headless=false
exit /b
