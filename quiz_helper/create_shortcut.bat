@echo off
rem Quiz Study Helper - create a desktop shortcut.
rem Double-click this file. For an "run as administrator" shortcut: create_shortcut.bat admin
setlocal
set "ARGS="
if /i "%~1"=="admin" set "ARGS=-Admin"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_shortcut.ps1" %ARGS%
echo.
pause
