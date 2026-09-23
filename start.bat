@echo off
rem ═══════════════════════════════════════════════════════════════════
rem  NORMAL START — no console window. Errors go to logs\jarvis.log
rem  (written by main.py). If nothing appears within ~10 seconds run
rem  start_debug.bat to see what is happening.
rem ═══════════════════════════════════════════════════════════════════
cd /d "%~dp0"

if exist "%~dp0venv\Scripts\pythonw.exe" (
    start "ICE" "%~dp0venv\Scripts\pythonw.exe" "%~dp0main.py"
    exit /b 0
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "ICE" pythonw "%~dp0main.py"
    exit /b 0
)

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "ICE" pyw "%~dp0main.py"
    exit /b 0
)

echo.
echo ERROR: pythonw.exe not found — cannot start in hidden mode.
echo Install Python 3.13+ from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH", then try again.
echo For a visible console with full logs run: start_debug.bat
echo.
pause
exit /b 1
