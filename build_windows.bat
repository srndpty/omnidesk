@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
if not defined SCRIPT_DIR set "SCRIPT_DIR=."
pushd "%SCRIPT_DIR%"

if not exist resources\icons\app_icon.ico (
    echo [ERROR] resources\icons\app_icon.ico is missing.
    exit /b 1
)

python -m ruff check . --no-cache
if errorlevel 1 exit /b 1

python -m pytest
if errorlevel 1 exit /b 1

pyinstaller --clean --noconfirm --workpath tmp\pyinstaller-build --distpath dist OmniDesk.spec
if errorlevel 1 exit /b 1

popd
endlocal
