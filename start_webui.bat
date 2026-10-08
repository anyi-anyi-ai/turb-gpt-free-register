@echo off
cd /d "%~dp0"
title Turb AI Registration Unified Mission Control (8799)

set PYTHON_EXE=python
if exist ".venv\Scripts\python.exe" set PYTHON_EXE=".venv\Scripts\python.exe"

echo ============================================================
echo   Turb AI Registration Unified Mission Control (8799)
echo   Opening: http://127.0.0.1:8799
echo ============================================================

start http://127.0.0.1:8799
%PYTHON_EXE% -m uvicorn webui.server:app --host 127.0.0.1 --port 8799

pause
