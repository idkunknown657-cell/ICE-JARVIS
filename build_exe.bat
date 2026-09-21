@echo off
rem Double-click build: produces dist\JARVIS\JARVIS.exe + staged runtime files.
py -3.12 build_exe.py %* 2>nul || python build_exe.py %*
echo.
pause
