# -*- coding: utf-8 -*-
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  GitHub 有头人机协同自动注册系统" -ForegroundColor Green
Write-Host "  驱动: CloakBrowser 独立指纹 + GeoIP + 自动取码" -ForegroundColor Yellow
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

$pythonCmd = "f:\github\turb-gpt-free-register\.venv\Scripts\python.exe"
if (-not (Test-Path $pythonCmd)) {
    $pythonCmd = "python"
}

& $pythonCmd interactive_github_register.py $args
