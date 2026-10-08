# UTF-8 Encoding
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "Turb AI Registration Unified Mission Control (8799)"

$PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $PSScriptRoot

$pythonExe = "python"
if (Test-Path "$PSScriptRoot\.venv\Scripts\python.exe") {
    $pythonExe = "$PSScriptRoot\.venv\Scripts\python.exe"
}

Write-Host "===============================================================================" -ForegroundColor Cyan
Write-Host "    Turb AI Registration Unified Mission Control (单一全能统一控制大屏)" -ForegroundColor Green
Write-Host "===============================================================================" -ForegroundColor Cyan
Write-Host "访问地址: http://127.0.0.1:8799" -ForegroundColor Yellow
Write-Host "功能涵盖: 运营大盘 | 调度流水线 | 跨平台资产金库 | 代理国家分流 | 基础设施中心"
Write-Host "===============================================================================" -ForegroundColor Cyan
Write-Host ""

Start-Process "http://127.0.0.1:8799"
& $pythonExe -m uvicorn webui.server:app --host 127.0.0.1 --port 8799
