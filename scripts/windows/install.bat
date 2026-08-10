@echo off
setlocal
cd /d "%~dp0\..\.."

set "PYTHON_VERSION=3.12"

where py >nul 2>nul
if errorlevel 1 (
    echo Python Launcher was not found. Install 64-bit Python %PYTHON_VERSION%.
    pause
    exit /b 1
)

py -%PYTHON_VERSION% -c "import platform; assert platform.architecture()[0] == '64bit'" >nul 2>nul
if errorlevel 1 (
    echo 64-bit Python %PYTHON_VERSION% was not found.
    echo Install it from https://www.python.org/downloads/windows/.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    py -%PYTHON_VERSION% -m venv .venv
    if errorlevel 1 (
        echo Could not create the virtual environment.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    echo Could not update pip.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pip install --no-cache-dir -r requirements.txt
if errorlevel 1 (
    echo Package installation failed.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -c "import discord; import requests; import dotenv; import sqlite3; c=sqlite3.connect(':memory:'); c.execute('CREATE VIRTUAL TABLE test USING fts5(content)'); print('Installation test passed - Discord', discord.__version__)"
if errorlevel 1 (
    echo Installation test failed.
    echo Check your Python installation and run install.bat again.
    pause
    exit /b 1
)

echo Installation complete. You can now run start.bat.
pause
