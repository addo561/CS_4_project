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

WHERE python >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python not found. Install Python 3.11 from python.org
    echo Then run:  pip install -r requirements.txt
    pause
    exit /b 1
)

python src\main.py
pause
