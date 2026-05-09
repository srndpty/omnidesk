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

python -m pyright
if errorlevel 1 exit /b 1

python -m pytest
if errorlevel 1 exit /b 1

if exist dist\OmniDesk rmdir /s /q dist\OmniDesk
if exist dist\OmniDesk.exe del /q dist\OmniDesk.exe
if exist dist\OmniDesk.zip del /q dist\OmniDesk.zip

pyinstaller --clean --noconfirm --workpath tmp\pyinstaller-build --distpath dist OmniDesk.spec
if errorlevel 1 exit /b 1

if not exist dist\OmniDesk\OmniDesk.exe (
    echo [ERROR] dist\OmniDesk\OmniDesk.exe was not created.
    exit /b 1
)

if not exist dist\OmniDesk\_internal (
    echo [ERROR] dist\OmniDesk\_internal was not created.
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\OmniDesk\*' -DestinationPath 'dist\OmniDesk.zip' -Force"
if errorlevel 1 exit /b 1

popd
endlocal
