# Turb AI Registration Unified Mission Control (8799)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "Turb AI Registration Unified Mission Control (8799)"
$RootDir = $PSScriptRoot
Set-Location $RootDir

# 1. 智能检测服务是否已在运行（防止多开端口冲突）
try {
    $checkRunning = Invoke-WebRequest -Uri "http://127.0.0.1:8799/api/status" -TimeoutSec 1 -UseBasicParsing -ErrorAction SilentlyContinue
    if ($checkRunning.StatusCode -eq 200) {
        Write-Host "============================================================" -ForegroundColor Cyan
        Write-Host "    Turb AI Registration Unified Mission Control (8799)" -ForegroundColor Green
        Write-Host "============================================================" -ForegroundColor Cyan
        Write-Host "WebUI 服务已在后台正常运行中，正在为您打开控制面板浏览器..." -ForegroundColor Yellow
        Start-Process "http://127.0.0.1:8799"
        Start-Sleep -Seconds 2
        exit 0
    }
} catch {}

# 2. 选择具备依赖的 Python 环境
$PythonExe = "python"
if (Test-Path "$RootDir\.venv\Scripts\python.exe") {
    $PythonExe = "$RootDir\.venv\Scripts\python.exe"
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "    Turb AI Registration Unified Mission Control (8799)" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "访问地址: http://127.0.0.1:8799" -ForegroundColor Yellow
Write-Host "网络出口: 本地 7890 节点 / Resin 粘性代理池 / GPTGrok2API 就绪" -ForegroundColor Gray
Write-Host "============================================================" -ForegroundColor Cyan

Start-Process "http://127.0.0.1:8799"
& $PythonExe -m uvicorn webui.server:app --host 127.0.0.1 --port 8799
