# Turb AI Registration Unified Mission Control (8799)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "Turb AI Registration Unified Mission Control (8799)"
$RootDir = $PSScriptRoot
Set-Location $RootDir

$PythonExe = "python"
if (Test-Path "$RootDir\.venv\Scripts\python.exe") {
    $PythonExe = "$RootDir\.venv\Scripts\python.exe"
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "    Turb AI Registration Unified Mission Control (8799)" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "访问地址: http://127.0.0.1:8799" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan

Start-Process "http://127.0.0.1:8799"
& $PythonExe -m uvicorn webui.server:app --host 127.0.0.1 --port 8799
