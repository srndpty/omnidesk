@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
if not defined SCRIPT_DIR set "SCRIPT_DIR=."

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\build-windows.ps1"
exit /b %ERRORLEVEL%
