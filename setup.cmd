@echo off
rem pc-tidy setup: double-click this once. It runs setup.ps1, allowing scripts for this one window
rem only (it changes no setting of Windows or PowerShell). Safe to run again.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "CODE=%ERRORLEVEL%"
echo %* | find /i "-NonInteractive" >nul || pause
exit /b %CODE%
