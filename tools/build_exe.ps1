# Builds the distributable ICE.exe (web-UI build) in one command.
#
#   powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
#
# Steps:
#  1. installs requirements-dev.txt (includes PyInstaller + the app deps)
#  2. runs build_exe.py → PyInstaller against ICE.spec → dist\ICE\ICE.exe
#     (+ _internal\ with the frozen runtime)
#  3. build_exe.py itself stages the runtime assets next to the exe:
#       ui_web\  actions\  core\prompt.txt  core\face_model.obj  core\face.obj
#
# Publishing to your users is a separate step — tools\publish_update.py.
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent

Write-Host "==> Installing dev + runtime dependencies..."
python -m pip install --quiet -r "$Root\requirements-dev.txt"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "==> Building ICE.exe (PyInstaller via build_exe.py)..."
python "$Root\build_exe.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

Write-Host ""
Write-Host "DONE: $Root\dist\ICE\ICE.exe"
Write-Host "Publish to your users:  python tools\publish_update.py --repo OWNER/REPO"
