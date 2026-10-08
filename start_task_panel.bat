@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Turb Registration Task Control Panel (8799)

set PYTHON_EXE=python
if exist ".venv\Scripts\python.exe" set PYTHON_EXE=".venv\Scripts\python.exe"

echo 正在启动注册机自动化流水线任务控制面板...
echo 访问地址: http://127.0.0.1:8799
echo 支持所有多模态媒体平台 (Suno, ElevenLabs, Udio, Stable Audio, Muse, RunningHub)
echo 与主力注册机 (ChatGPT, Grok, Claude, Kiro, GitHub, Outlook) 的可视化调度管理与实时日志查看
echo.

start http://127.0.0.1:8799
%PYTHON_EXE% -m uvicorn webui.server:app --host 127.0.0.1 --port 8799
pause
