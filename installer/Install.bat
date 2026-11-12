@echo off
rem Metaphor: ReFantazio Remaster - double-click to install.
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\Install.ps1" %*
echo.
pause
