# -*- coding: utf-8 -*-
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "100GB Proxy Auto-Harvester (Resin Pipeline)"

$PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $PSScriptRoot

Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "  🌟 100GB 纯净代理自动化获取器 (Proxy Auto-Harvester)" -ForegroundColor Green
Write-Host "  规格: 10 账号 x 100 节点 = 1,000 节点 / 100GB" -ForegroundColor Yellow
Write-Host "  流程: 级联邮箱 -> Resin出口注册 -> 1000节点落盘 -> 自动触发 Resin 热更" -ForegroundColor Yellow
Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host ""

python run_proxy_harvester.py @args
