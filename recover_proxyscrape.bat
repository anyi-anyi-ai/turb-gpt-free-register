@echo off
chcp 65001 >nul
title ProxyScrape Recover and Extract Assets
cd /d "%~dp0"

set PYTHON_EXE=python
if exist ".venv\Scripts\python.exe" set PYTHON_EXE=".venv\Scripts\python.exe"

echo ==============================================================================
echo   🌐 ProxyScrape 已注册账号一键会话恢复与节点提取器
echo   目标: 自动登录已成功注册账号 (LaurenEstrada, SavannahCollins, KarlBrown 等)
echo   流程: 自动登录/接码 -^> 激活试用 -^> 调用协议修复接口 -^> 提取 100 节点落盘
echo ==============================================================================
echo.

%PYTHON_EXE% recover_and_extract_proxyscrape.py %*

echo.
pause
