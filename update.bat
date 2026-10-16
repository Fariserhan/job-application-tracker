@echo off
REM ============================================================
REM  Job Search Analytics - one-click update (Windows)
REM  Downloads the latest version, installs what it needs, reads
REM  Gmail, then opens the app. Safe to run any time.
REM ============================================================
setlocal
REM Run from a temporary copy: cmd reads a .bat while executing it, so the git pull
REM below must never rewrite the file that is running.
if /i "%~1"=="--from-copy" goto :run
copy /y "%~f0" "%TEMP%\jobtracker-update.bat" >nul
"%TEMP%\jobtracker-update.bat" --from-copy "%~dp0"
:run
title Job Search Analytics - update
cd /d "%~2"
set "PY=venv\Scripts\python.exe"

echo.
echo  [1/4] Downloading the latest version...
where git >nul 2>nul
if errorlevel 1 (
    echo        Git is not installed, so this step is skipped.
) else (
    git pull --ff-only origin master
    if errorlevel 1 goto :pullfail
)

echo.
echo  [2/4] Checking Python...
if exist "%PY%" (
    "%PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
    if errorlevel 1 (
        echo        The old Python environment is too old - rebuilding it.
        rmdir /s /q venv
    )
)
if not exist "%PY%" (
    py -3.12 -m venv venv 2>nul || py -3 -m venv venv 2>nul || python -m venv venv
)
if not exist "%PY%" goto :nopython
"%PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 goto :nopython

echo.
echo  [3/4] Installing what it needs (the first time takes a few minutes)...
"%PY%" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :pipfail

echo.
echo  [4/4] Reading your Gmail (a browser window may ask you to sign in to Google)...
REM The first run after upgrading re-reads the whole mailbox so every email is judged by
REM the current parser; later runs only fetch new mail.
set "SYNCARGS="
if not exist ".full-sync-done" set "SYNCARGS=--full"
"%PY%" -m jobtracker sync %SYNCARGS%
set "SYNCERR=%errorlevel%"
if "%SYNCERR%"=="0" if not exist ".full-sync-done" type nul > ".full-sync-done"
if not "%SYNCERR%"=="0" (
    echo.
    echo  The Gmail sync did not finish - the app will still open with your existing data.
    echo  Take a screenshot of the message above if you want help with it.
    pause
)

echo.
echo  All done. Opening the app...
call launch_app.bat
exit /b 0

:pullfail
echo.
echo  Could not download the update - see the message above.
echo  Take a screenshot of this window and send it for help. Nothing was changed.
pause
exit /b 1

:nopython
echo.
echo  Python 3.11 or newer is needed. Install Python 3.12 from https://www.python.org/downloads/
echo  (tick "Add python.exe to PATH" during setup), then run update.bat again.
pause
exit /b 1

:pipfail
echo.
echo  Installing the requirements failed - see the message above.
echo  Check your internet connection and run update.bat again.
pause
exit /b 1
