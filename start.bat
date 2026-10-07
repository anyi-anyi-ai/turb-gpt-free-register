@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Turb GPT Free Register WebUI

if not exist ".venv\Scripts\python.exe" goto :no_venv

echo 正在启动 Turb GPT Free Register WebUI...
echo 访问地址: http://127.0.0.1:5005
echo 访问授权码: 请参阅 .env 文件中的 WEBUI_AUTH_CODE 配置
echo 正在启动 Azure 云端自愈穿透隧道 (端口 5005 映射)...
start /min "Azure SelfHeal Tunnel" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cloud_tunnel_selfheal.ps1"
echo.

".venv\Scripts\python.exe" web.py --port 5005 --open-browser
pause
exit /b 0

:no_venv
echo [错误] 未检测到 .venv 虚拟环境！
pause
exit /b 1
