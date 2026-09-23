@echo off
rem ═══════════════════════════════════════════════════════════════════
rem  DEBUG START — console stays visible, every print + traceback is
rem  shown here AND saved to logs\jarvis.log. Use this when something
rem  is not working.
rem ═══════════════════════════════════════════════════════════════════
cd /d "%~dp0"
if not exist "%~dp0logs" mkdir "%~dp0logs"

echo === ICE JARVIS — debug start ===
echo Working dir: %~dp0
echo Log file   : %~dp0logs\jarvis.log
echo.

set "PY="
if exist "%~dp0venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    where py >nul 2>nul && set "PY=py"
)
if not defined PY (
    echo ERROR: Python not found.
    echo Install Python 3.13+ from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

"%PY%" --version
echo.
"%PY%" -u main.py
set RC=%errorlevel%
if not "%RC%"=="0" (
    echo.
    echo JARVIS exited with error code %RC%.
    echo Full traceback: %~dp0logs\jarvis.log
    echo.
    pause
)
exit /b %RC%
