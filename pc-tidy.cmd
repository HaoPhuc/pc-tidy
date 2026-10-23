@echo off
rem pc-tidy: double-click to see what fills C: and free space on it, safely.
rem "Run as administrator" works too (it starts in System32, so this goes to its own folder first),
rem and lets pc-tidy measure and clean the Windows folders as well.
setlocal
cd /d "%~dp0"
set "PY="
if defined PCTIDY_PYTHON call :try "%PCTIDY_PYTHON%"
if not defined PY call :try "%~dp0.venv\Scripts\python.exe"
if not defined PY call :try py -3
if not defined PY call :try python
if not defined PY (
    echo pc-tidy needs Python 3.9 or newer, and none was found here.
    echo Install it as README.md says ^(step 1^), then double-click pc-tidy.cmd again.
    echo.
    pause
    exit /b 1
)
%PY% "%~dp0pc_tidy.py" %*
set "CODE=%ERRORLEVEL%"
echo.
pause
exit /b %CODE%

rem A Python counts only if it runs and is 3.9 or newer: the Microsoft Store's python.exe only says
rem "Python was not found".
:try
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set PY=%*
exit /b 0
