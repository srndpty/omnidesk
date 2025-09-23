@echo off
setlocal

if not defined VENV_DIR (
    for %%i in (.) do set ROOT_DIR=%%~fi
) else (
    set ROOT_DIR=%VENV_DIR%
)

pyinstaller --clean --noconfirm OmniDesk.spec

endlocal
