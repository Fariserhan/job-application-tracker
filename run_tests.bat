@echo off
REM ============================================================
REM  Job Application Suite - test runner
REM  Runs the hermetic unit + headless dashboard smoke tests.
REM  No personal data is touched (temporary DB/CV/AI cache).
REM ============================================================
cd /d "%~dp0"
echo.
echo  Running the test suite...
echo.
venv\Scripts\python.exe -m unittest discover -s tests -v
echo.
pause
