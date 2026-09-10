@echo off
REM ============================================================
REM  Job Application Suite - iPad / Phone launcher
REM  Binds the dashboard to your Wi-Fi network so any device on
REM  the same network can open it. The exact URL + QR code is
REM  shown inside the dashboard sidebar ("Open on iPad").
REM ============================================================
cd /d "%~dp0"
echo.
echo  Starting Job Application Suite (LAN-accessible)...
echo  On your iPad: open Safari and go to the URL shown in the
echo  sidebar under "Open on iPad / Phone" (scan the QR code).
echo.
venv\Scripts\streamlit.exe run dashboard.py --server.address 0.0.0.0 --server.port 8501
