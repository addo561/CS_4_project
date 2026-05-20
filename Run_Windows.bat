@echo off
REM =============================================================
REM  System Resource Optimizer — Windows Launcher
REM  Double-click this file in File Explorer to start the app.
REM =============================================================

SET SCRIPT_DIR=%~dp0

REM Try the pre-built .exe first
SET EXE=%SCRIPT_DIR%dist\SystemResourceOptimizer.exe
IF EXIST "%EXE%" (
    echo Launching System Resource Optimizer...
    start "" "%EXE%"
    exit /b 0
)

REM Fallback: run from Python source
echo App .exe not found -- running from source...
cd /d "%SCRIPT_DIR%"

REM Check if Python is installed
WHERE python >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python not found. Install Python 3.10+ from python.org
    pause
    exit /b 1
)

REM Check if venv exists, create if not
IF NOT EXIST "%SCRIPT_DIR%venv\" (
    echo Creating virtual environment...
    python -m venv "%SCRIPT_DIR%venv"
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        echo Please ensure Python 3.10+ is installed from https://python.org
        pause
        exit /b 1
    )
    echo Virtual environment created successfully.
)

REM Activate venv and install/check requirements
echo Checking and installing dependencies...
call "%SCRIPT_DIR%venv\Scripts\activate.bat"
pip install -q -r "%SCRIPT_DIR%requirements.txt"
if errorlevel 1 (
    echo ERROR: Failed to install/verify dependencies.
    echo Check your internet connection.
    pause
    exit /b 1
)

REM Run the app
echo Launching System Resource Optimizer...
python src\main.py
pause
