@echo off
rem ═══════════════════════════════════════════════════════════════════
rem  FIRST-TIME SETUP — finds a supported Python (3.13+), creates a
rem  venv if needed, installs dependencies, checks config and starts
rem  JARVIS. Every failure is printed here AND written to
rem  logs\setup.log — nothing fails silently.
rem ═══════════════════════════════════════════════════════════════════
setlocal enabledelayedexpansion
cd /d "%~dp0"
if not exist "%~dp0logs" mkdir "%~dp0logs"

echo === ICE JARVIS setup ===
echo Working dir: %~dp0
echo.

rem ── 1. Find Python 3.13 or newer ───────────────────────────────────
set "PYCAND="
python -c "import sys;sys.exit(0 if sys.version_info>=(3,13) else 1)" >nul 2>nul && set "PYCAND=python"
if not defined PYCAND py -3.14 -c "import sys;sys.exit(0 if sys.version_info>=(3,13) else 1)" >nul 2>nul && set "PYCAND=py -3.14"
if not defined PYCAND py -3.13 -c "import sys;sys.exit(0 if sys.version_info>=(3,13) else 1)" >nul 2>nul && set "PYCAND=py -3.13"
if not defined PYCAND py -c "import sys;sys.exit(0 if sys.version_info>=(3,13) else 1)" >nul 2>nul && set "PYCAND=py"

if not defined PYCAND (
    echo.
    echo ERROR: Python 3.13 or newer was not found on this machine.
    echo.
    echo   - Download it: https://www.python.org/downloads/
    echo   - During install TICK: "Add python.exe to PATH"
    echo   - Close this window, reopen it, run setup.bat again
    echo.
    echo Already have an older Python ^(e.g. 3.11/3.12^)? Setup needs 3.13+.
    echo This message is also saved to %~dp0logs\setup.log
    echo ERROR: Python 3.13+ not found on PATH> "%~dp0logs\setup.log"
    pause
    exit /b 1
)

rem Resolve the candidate to a full python.exe path so every later call is
rem a single quoted executable (works even if the folder has spaces).
set "PYEXE="
for /f "delims=" %%i in ('%PYCAND% -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
if not defined PYEXE (
    echo ERROR: could not resolve the Python executable.>> "%~dp0logs\setup.log"
    echo ERROR: could not resolve the Python executable.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('"%PYEXE%" --version 2^>nul') do set "PYVER=%%v"
echo Found %PYVER% at %PYEXE%
echo.

rem ── 2. Choose the interpreter: existing venv > system if deps exist > new venv
set "RUN=%PYEXE%"
if exist "%~dp0venv\Scripts\python.exe" (
    set "RUN=%~dp0venv\Scripts\python.exe"
    echo Using existing venv: %~dp0venv
    goto :deps
)
"%PYEXE%" -c "import webview, psutil" >nul 2>nul
if not errorlevel 1 (
    echo Dependencies already present on the system Python - no venv needed.
    goto :deps
)
echo Creating virtual environment: %~dp0venv
"%PYEXE%" -m venv "%~dp0venv"
if not exist "%~dp0venv\Scripts\python.exe" (
    echo ERROR: could not create the virtual environment.>> "%~dp0logs\setup.log"
    echo ERROR: could not create the virtual environment.
    echo        Details are in logs\setup.log
    pause
    exit /b 1
)
set "RUN=%~dp0venv\Scripts\python.exe"
echo Virtual environment ready.

:deps
rem ── 3. Install / verify dependencies ──────────────────────────────
echo Installing dependencies ^(this can take a few minutes^)...
"%RUN%" "%~dp0setup.py"
if errorlevel 1 (
    echo.
    echo ERROR: setup.py failed. Full details:
    echo     %~dp0logs\setup.log
    echo.
    pause
    exit /b 1
)
echo Dependencies OK.
echo.

rem ── 4. Config check ───────────────────────────────────────────────
if exist "%~dp0config\api_keys.json" (
    echo Config found: config\api_keys.json
) else (
    echo No config yet - the in-app setup screen will appear on first start.
    echo You can start without any API key and add one later in
    echo Settings - API Keys.
)
echo.

rem ── 5. Start JARVIS ───────────────────────────────────────────────
echo Starting JARVIS...
if exist "%~dp0venv\Scripts\pythonw.exe" (
    start "ICE" "%~dp0venv\Scripts\pythonw.exe" "%~dp0main.pyw"
) else (
    start "ICE" "%~dp0main.pyw"
)
exit /b 0
