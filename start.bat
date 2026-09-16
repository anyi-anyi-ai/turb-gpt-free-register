@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Turb GPT Free Register WebUI

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未检测到 .venv 虚拟环境！
    pause
    exit /b 1
)

echo 正在启动 Turb GPT Free Register WebUI...
echo 访问地址: http://127.0.0.1:5005
echo 默认授权码: admin123 (可在 .env 文件或页面中修改)
echo.

".venv\Scripts\python.exe" web.py --port 5005 --open-browser
pause
