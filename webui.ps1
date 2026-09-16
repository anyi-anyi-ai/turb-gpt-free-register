# Turb GPT Free Register WebUI Windows 管理脚本 (PowerShell)
#
# 用法：
#   .\webui.ps1 start      启动 WebUI (后台运行)
#   .\webui.ps1 stop       关闭 WebUI
#   .\webui.ps1 restart    重启 WebUI
#   .\webui.ps1 status     查看运行状态
#   .\webui.ps1 logs       实时查看日志
#   .\webui.ps1 run        前台运行 (直显日志)

param (
    [Parameter(Position=0)]
    [ValidateSet("start", "stop", "restart", "status", "logs", "run", "help")]
    [string]$Command = "help",

    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 5005,
    [switch]$OpenBrowser,
    [switch]$VerboseLog,
    [string]$AuthCode = ""
)

$RootDir = $PSScriptRoot
$RunDir = Join-Path $RootDir "run"
$LogDir = Join-Path $RootDir "logs"
$PidFile = Join-Path $RunDir "webui.pid"
$LogFile = Join-Path $LogDir "webui.log"

if (-not (Test-Path $RunDir)) { New-Item -ItemType Directory -Path $RunDir -Force | Out-Null }
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

$PythonExe = Join-Path $RootDir ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        $PythonExe = $cmd.Source
    } else {
        Write-Error "未找到 Python。请先在项目下创建 .venv 虚拟环境。"
        exit 1
    }
}

function Get-RunningProcess {
    if (Test-Path $PidFile) {
        $savedPid = Get-Content $PidFile -Raw -ErrorAction SilentlyContinue
        if ($savedPid -and ($savedPid.Trim() -match '^\d+$')) {
            $p = Get-Process -Id ([int]$savedPid.Trim()) -ErrorAction SilentlyContinue
            if ($p) {
                return $p
            }
        }
    }
    return $null
}

function Start-WebUI {
    $proc = Get-RunningProcess
    if ($proc) {
        Write-Host "WebUI 已在运行中：PID=$($proc.Id)，地址：http://${HostAddress}:${Port}" -ForegroundColor Yellow
        return
    }

    $argList = @("-u", "web.py", "--host", $HostAddress, "--port", "$Port")
    if ($OpenBrowser) { $argList += "--open-browser" }
    if ($VerboseLog) { $argList += "--verbose" }
    if ($AuthCode) { $argList += @("--auth-code", $AuthCode) }

    Write-Host "正在启动 WebUI：http://${HostAddress}:${Port}" -ForegroundColor Cyan
    Write-Host "日志输出至：$LogFile" -ForegroundColor Cyan

    $ErrFile = Join-Path $LogDir "webui.err.log"
    $startParams = @{
        FilePath = $PythonExe
        ArgumentList = $argList
        WorkingDirectory = $RootDir
        RedirectStandardOutput = $LogFile
        RedirectStandardError = $ErrFile
        WindowStyle = "Hidden"
        PassThru = $true
    }
    $proc = Start-Process @startParams

    Set-Content -Path $PidFile -Value $proc.Id

    Start-Sleep -Seconds 2
    if (-not $proc.HasExited) {
        Write-Host "启动成功！PID=$($proc.Id)" -ForegroundColor Green
        Write-Host "访问地址：http://${HostAddress}:${Port}" -ForegroundColor Green
    } else {
        Write-Host "启动失败，请查看日志文件：$LogFile" -ForegroundColor Red
        if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
    }
}

function Run-WebUIForeground {
    $argList = @("-u", "web.py", "--host", $HostAddress, "--port", "$Port")
    if ($OpenBrowser) { $argList += "--open-browser" }
    if ($VerboseLog) { $argList += "--verbose" }
    if ($AuthCode) { $argList += @("--auth-code", $AuthCode) }

    Write-Host "前台运行 WebUI：http://${HostAddress}:${Port}" -ForegroundColor Cyan
    & $PythonExe @argList
}

function Stop-WebUI {
    $proc = Get-RunningProcess
    if (-not $proc) {
        Write-Host "WebUI 未在运行。" -ForegroundColor Yellow
        if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
        return
    }

    Write-Host "正在关闭 WebUI (PID=$($proc.Id))..." -ForegroundColor Cyan
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
    Write-Host "已关闭 WebUI。" -ForegroundColor Green
}

function Show-Status {
    $proc = Get-RunningProcess
    if ($proc) {
        Write-Host "WebUI 状态：运行中 (PID=$($proc.Id))" -ForegroundColor Green
        Write-Host "访问地址：http://${HostAddress}:${Port}" -ForegroundColor Cyan
        Write-Host "日志文件：$LogFile" -ForegroundColor Gray
    } else {
        Write-Host "WebUI 状态：未运行" -ForegroundColor Yellow
    }
}

function Show-Logs {
    if (-not (Test-Path $LogFile)) {
        Write-Host "日志文件尚不存在：$LogFile" -ForegroundColor Yellow
        return
    }
    Get-Content -Path $LogFile -Tail 50 -Wait
}

switch ($Command) {
    "start"   { Start-WebUI }
    "run"     { Run-WebUIForeground }
    "stop"    { Stop-WebUI }
    "restart" { Stop-WebUI; Start-Sleep -Seconds 1; Start-WebUI }
    "status"  { Show-Status }
    "logs"    { Show-Logs }
    default {
        Write-Host "Turb GPT Free Register 管理脚本"
        Write-Host ""
        Write-Host "用法："
        Write-Host "  .\webui.ps1 start [-Port 5000] [-OpenBrowser]   后台启动 WebUI"
        Write-Host "  .\webui.ps1 run                                 前台启动 WebUI (直显输出)"
        Write-Host "  .\webui.ps1 stop                                停止 WebUI"
        Write-Host "  .\webui.ps1 restart                             重启 WebUI"
        Write-Host "  .\webui.ps1 status                              查看运行状态"
        Write-Host "  .\webui.ps1 logs                                实时查看日志"
    }
}
