@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Azure 云端自愈穿透隧道 (Port 5005)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cloud_tunnel_selfheal.ps1"
pause
