@echo off
cd /d "%~dp0"
title GitHub Auto Register Assistant

if not exist ".venv\Scripts\python.exe" goto :no_venv

".venv\Scripts\python.exe" tools\launch_github.py

if errorlevel 1 (
    echo.
    echo [ERROR] Program exited with error. Press any key to close...
    pause >nul
)
exit /b 0

:no_venv
echo [ERROR] Virtual environment .venv not found in current directory!
pause
exit /b 1
