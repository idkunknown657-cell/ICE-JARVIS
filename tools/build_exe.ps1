# Builds the distributable JARVIS.exe in one command.
#
#   powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
#
# Steps:
#  1. installs requirements-dev.txt (PyInstaller + the app dependencies)
#  2. runs build_exe.py → PyInstaller against JARVIS.spec → dist\JARVIS\JARVIS.exe
#     (+ _internal\ with the frozen runtime)
#  3. build_exe.py itself stages the runtime assets next to the exe:
#       ui_web\  actions\  plugins\  core\prompt.txt  core\face_model.obj
#
# Publishing to your users is the next step — tools\publish_update.py.
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent

Write-Host "==> Installing dev + runtime dependencies..."
python -m pip install --quiet -r "$Root\requirements-dev.txt"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "==> Building JARVIS.exe (PyInstaller via build_exe.py)..."
python "$Root\build_exe.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

Write-Host ""
Write-Host "DONE: $Root\dist\JARVIS\JARVIS.exe"
Write-Host "Publish to your users:  python tools\publish_update.py --repo OWNER/REPO"
