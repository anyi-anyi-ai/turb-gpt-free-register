# Turb GPT Free Register Task Control Panel Launcher (8799)
$RootDir = $PSScriptRoot

$PythonExe = "python"
if (Test-Path "$RootDir\.venv\Scripts\python.exe") {
    $PythonExe = "$RootDir\.venv\Scripts\python.exe"
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "正在启动注册机自动化流水线任务控制面板 (WebUI :8799)..." -ForegroundColor Cyan
Write-Host "访问地址: http://127.0.0.1:8799" -ForegroundColor Green
Write-Host "支持所有多模态平台 (Suno, ElevenLabs, Udio, Stable Audio, Muse, RunningHub)" -ForegroundColor Yellow
Write-Host "与主力注册机 (ChatGPT, Grok, Claude, Kiro, GitHub, Outlook) 的可视化调度管理" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan

Start-Process "http://127.0.0.1:8799"
& $PythonExe -m uvicorn webui.server:app --host 127.0.0.1 --port 8799
