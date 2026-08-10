@echo off
setlocal
cd /d "%~dp0\..\.."

if not exist ".venv\Scripts\python.exe" (
    echo .venv was not found.
    echo Run install.bat first.
    pause
    exit /b 1
)

set "PYTHONDONTWRITEBYTECODE=1"
".venv\Scripts\python.exe" -m app
pause
