@echo off
REM Runs the hermetic test suite, lint and the parser accuracy gate. No personal data is touched.
cd /d "%~dp0"
venv\Scripts\python.exe -m pytest
venv\Scripts\python.exe -m ruff check .
venv\Scripts\python.exe -m jobtracker eval
pause
