# Builds the distributable ICE.exe (and the update manifest) on Windows.
#
#   powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
#
# Steps:
#   1. installs requirements-dev.txt (includes PyInstaller + the app deps)
#   2. runs PyInstaller against ICE.spec  ->  dist\ICE.exe
#   3. copies the runtime folders/files the exe reads from its own directory:
#        core\prompt.txt   core\face_model.obj   actions\   plugins\
#
# Publishing to your users is a separate step — see tools\publish_update.py.

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent

Write-Host "==> Installing dev + runtime dependencies..."
python -m pip install --quiet -r "$Root\requirements-dev.txt"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "==> Building ICE.exe (PyInstaller)..."
python -m PyInstaller --noconfirm --clean "$Root\ICE.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

Write-Host "==> Copying runtime assets next to the exe..."
$Dist  = Join-Path $Root "dist"
Copy-Item -Force (Join-Path $Root "core\prompt.txt")    (Join-Path $Dist "core\prompt.txt")
Copy-Item -Force (Join-Path $Root "core\face_model.obj") (Join-Path $Dist "core\face_model.obj")
Copy-Item -Recurse -Force (Join-Path $Root "actions") (Join-Path $Dist "actions")
Copy-Item -Recurse -Force (Join-Path $Root "plugins") (Join-Path $Dist "plugins")

Write-Host ""
Write-Host "DONE: $Dist\ICE.exe" -ForegroundColor Green
Write-Host "To publish this build to your users:" -ForegroundColor Yellow
Write-Host "  python tools\publish_update.py --repo OWNER/REPO [--version X.Y.Z]"