# ProxyScrape 自动化代理获取脚本 (有头模式 + 投屏协同)
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$PythonExe = "python"
if (Test-Path ".venv\Scripts\python.exe") {
    $PythonExe = ".venv\Scripts\python.exe"
}

Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "  🌐 ProxyScrape 自动化代理获取器 (有头模式 + 实时投屏协同)" -ForegroundColor Cyan
Write-Host "  目标: ProxyScrape 7天锁定 100 节点 / 10GB 纯净代理" -ForegroundColor Cyan
Write-Host "  模式: 有头浏览器 (Headed) + 实时投屏协同 (http://127.0.0.1:8799/live)" -ForegroundColor Cyan
Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "💡 提示: 弹出浏览器窗口后，如遇 Cloudflare Turnstile 人机验证，系统将自动尝试点击，您也可手动点击辅助秒过。" -ForegroundColor Yellow
Write-Host ""

$env:PROXY_HARVESTER_PROVIDER = "proxyscrape"
& $PythonExe run_proxy_harvester.py --provider proxyscrape --accounts 1 --headed $args
