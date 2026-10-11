# -*- coding: utf-8 -*-
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$root = $PSScriptRoot
Set-Location $root

$pythonBin = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonBin)) {
    $pythonBin = "f:\github\turb-gpt-free-register\.venv\Scripts\python.exe"
}
if (-not (Test-Path $pythonBin)) {
    $pythonBin = "python"
}

function Show-Menu {
    Clear-Host
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host "  🚀 GitHub 有头人机协同自动注册工作台 (CloakBrowser 独立指纹)" -ForegroundColor Green
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  [1] 立即开始单号注册 (默认：自动调度代理 / 1 个账号 / 协同验证)" -ForegroundColor White
    Write-Host "  [2] 连续批量注册模式 (自动连续循环推进，直到邮箱耗尽或主动退出)" -ForegroundColor Yellow
    Write-Host "  [3] 本机直连 / 全局梯子模式 (--no-proxy，适合本机已开启全局 VPN)" -ForegroundColor White
    Write-Host "  [4] 手动指定代理节点 (如 http://127.0.0.1:7890)" -ForegroundColor White
    Write-Host "  [5] 指定单个邮箱注册测试" -ForegroundColor White
    Write-Host "  [6] 查看当前数据库与待注册邮箱池状态" -ForegroundColor Cyan
    Write-Host "  [0] 退出" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "======================================================================" -ForegroundColor Cyan
}

while ($true) {
    Show-Menu
    $choice = Read-Host "请输入选项序号 [1-6, 0]"

    switch ($choice) {
        "1" {
            Clear-Host
            Write-Host "[模式 1] 正在启动单号有头人机协同注册..." -ForegroundColor Green
            & $pythonBin interactive_github_register.py --count 1
            Read-Host "`n执行已结束，按回车返回主菜单..."
        }
        "2" {
            Clear-Host
            Write-Host "[模式 2] 正在启动连续批量人机协同注册..." -ForegroundColor Yellow
            & $pythonBin interactive_github_register.py --count 0
            Read-Host "`n执行已结束，按回车返回主菜单..."
        }
        "3" {
            Clear-Host
            Write-Host "[模式 3] 正在启动直连模式 (不注入独立代理)..." -ForegroundColor White
            & $pythonBin interactive_github_register.py --no-proxy --count 1
            Read-Host "`n执行已结束，按回车返回主菜单..."
        }
        "4" {
            Clear-Host
            $proxyInput = Read-Host "请输入代理地址 (默认 http://127.0.0.1:7890)"
            if ([string]::IsNullOrWhiteSpace($proxyInput)) { $proxyInput = "http://127.0.0.1:7890" }
            Write-Host "正在使用代理 $proxyInput 启动..." -ForegroundColor Green
            & $pythonBin interactive_github_register.py --proxy $proxyInput --count 1
            Read-Host "`n执行已结束，按回车返回主菜单..."
        }
        "5" {
            Clear-Host
            $emailInput = Read-Host "请输入待注册的特定 Outlook 邮箱"
            if ([string]::IsNullOrWhiteSpace($emailInput)) {
                Write-Host "邮箱不能为空！" -ForegroundColor Red
            } else {
                & $pythonBin interactive_github_register.py --email $emailInput --count 1
            }
            Read-Host "`n执行已结束，按回车返回主菜单..."
        }
        "6" {
            Clear-Host
            & $pythonBin interactive_github_register.py --status
            Read-Host "`n按回车返回主菜单..."
        }
        "0" {
            Write-Host "正在退出..." -ForegroundColor Gray
            exit 0
        }
        default {
            Write-Host "⚠️ 无效输入，请重新选择。" -ForegroundColor Yellow
            Start-Sleep -Seconds 1
        }
    }
}
