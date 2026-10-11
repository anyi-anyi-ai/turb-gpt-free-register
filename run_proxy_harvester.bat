@echo off
chcp 65001 >nul
title 100GB Proxy Auto-Harvester (Resin Pipeline)
cd /d "%~dp0"

echo ==============================================================================
echo   🌟 100GB 纯净代理自动化获取器 (Proxy Auto-Harvester)
echo   规格: 10 账号 x 100 节点 = 1,000 节点 / 100GB
echo   流程: 级联邮箱 -^> Resin出口注册 -^> 1000节点落盘 -^> 自动触发 Resin 热更
echo ==============================================================================
echo.

python run_proxy_harvester.py %*

echo.
pause
