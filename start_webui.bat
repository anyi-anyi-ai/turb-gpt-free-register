@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Turb AI Registration Unified Mission Control (8799)

set PYTHON_EXE=python
if exist ".venv\Scripts\python.exe" set PYTHON_EXE=".venv\Scripts\python.exe"

echo ===============================================================================
echo     Turb AI Registration Unified Mission Control (单一全能统一控制大屏)
echo ===============================================================================
echo 访问地址: http://127.0.0.1:8799
echo 支持功能:
echo   - 📊 全局运营大盘 (跨平台账号资产水位、活跃注册流水线、系统监控)
echo   - 🚀 调度流水线 (ChatGPT, Grok, GitHub, Suno, ElevenLabs, Udio 等 10+ 平台调度与实时日志)
echo   - 🏦 资产金库 (Universal Vault 全平台凭据管理、Secret 安全保护、Sub2API/OpenAI 导出)
echo   - 🌐 网络出口 (Clash 节点测速/轮换、代理国家分流与住宅权重调优)
echo   - @ 基础设施 (邮箱池批量导入、.env 安全编辑、K12 与 Plus 工作台)
echo ===============================================================================
echo.

start http://127.0.0.1:8799
%PYTHON_EXE% -m uvicorn webui.server:app --host 127.0.0.1 --port 8799
pause
