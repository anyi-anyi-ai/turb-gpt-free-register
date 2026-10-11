@echo off
cd /d "%~dp0"
title GitHub Auto Register (Single Runner)

if not exist ".venv\Scripts\python.exe" goto :no_venv

".venv\Scripts\python.exe" interactive_github_register.py --count 1

if errorlevel 1 (
    echo.
    echo [ERROR] Execution failed. Press any key to close...
    pause >nul
)
exit /b 0

:no_venv
echo [ERROR] Virtual environment .venv not found in current directory!
pause
exit /b 1
