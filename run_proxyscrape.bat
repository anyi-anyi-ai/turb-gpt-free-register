@echo off
chcp 65001 >nul
title ProxyScrape 100-Node Harvester (Headed + Live Cast)
cd /d "%~dp0"

set PYTHON_EXE=python
if exist ".venv\Scripts\python.exe" set PYTHON_EXE=".venv\Scripts\python.exe"

echo ==============================================================================
echo   🌐 ProxyScrape 自动化代理获取器 (有头模式 + 实时投屏协同)
echo   目标: ProxyScrape 7天锁定 100 节点 / 10GB 纯净代理
echo   模式: 有头浏览器 (Headed) + 实时投屏协同 (http://127.0.0.1:8799/live)
echo   流程: 邮箱分配 -^> 拟人填表 -^> Cloudflare Turnstile 求解 -^> 邮件激活 -^> 节点导出
echo ==============================================================================
echo.
echo 💡 提示:
echo 1. 脚本将自动启动真实的浏览器窗口，并自动填写邮箱、密码与条款；
echo 2. 如遇 Cloudflare Turnstile 人机验证，系统会自动尝试物理拟人点击；
echo 3. 您也可直接在弹出的浏览器窗口，或访问 http://127.0.0.1:8799/live 手动勾选协助。
echo.

set PROXY_HARVESTER_PROVIDER=proxyscrape
%PYTHON_EXE% run_proxy_harvester.py --provider proxyscrape --accounts 1 --headed %*

echo.
if errorlevel 1 (
    echo ❌ 获取失败或任务中断。
    pause
) else (
    echo ✅ 获取完成！
    pause
)
