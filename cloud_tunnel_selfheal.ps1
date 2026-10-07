# -*- coding: utf-8 -*-
# Azure 云端 gptGrok2api <-> 本地注册机 自愈反向隧道 (SSH Reverse Tunnel)
# 作用：将 Azure 云端 VM 的 5005 端口流量穿透转发回本地 127.0.0.1:5005，
# 使云端 gptGrok2api 发生 Token 失效时，能无缝调用本地注册机触发自动查活/自愈重登。

# 加载同目录 .env 文件配置
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$envPath = Join-Path $scriptDir ".env"
if (Test-Path $envPath) {
    Get-Content $envPath | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $parts = $line.Split("=", 2)
            $varName = $parts[0].Trim()
            $varVal = $parts[1].Trim()
            if (-not [System.Environment]::GetEnvironmentVariable($varName)) {
                [System.Environment]::SetEnvironmentVariable($varName, $varVal, "Process")
            }
        }
    }
}

param (
    [string]$VmIp = $env:AZURE_VM_IP,
    [string]$VmUser = ($env:AZURE_VM_USER ? $env:AZURE_VM_USER : "azureuser"),
    [string]$KeyPath = ($env:AZURE_SSH_KEY_PATH ? $env:AZURE_SSH_KEY_PATH : "$HOME\.ssh\azure_id_ed25519"),
    [int]$RemotePort = ($env:AZURE_TUNNEL_REMOTE_PORT ? [int]$env:AZURE_TUNNEL_REMOTE_PORT : 5005),
    [int]$LocalPort = ($env:AZURE_TUNNEL_LOCAL_PORT ? [int]$env:AZURE_TUNNEL_LOCAL_PORT : 5005)
)

if (-not $VmIp) {
    Write-Host "[错误] 未指定 Azure VM IP。请在 .env 中设置 AZURE_VM_IP 或使用 -VmIp 参数。" -ForegroundColor Red
    exit 1
}

$Host.UI.RawUI.WindowTitle = "Azure 自愈反向隧道 [:$RemotePort -> 127.0.0.1:$LocalPort]"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  Azure 云端 gptGrok2api <-> 本地注册机 自愈反向隧道守护" -ForegroundColor Green
Write-Host "  云端目标: ${VmUser}@${VmIp}" -ForegroundColor Yellow
Write-Host "  端口映射: 云端 VM 127.0.0.1:${RemotePort} ---> 本地 127.0.0.1:${LocalPort}" -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $KeyPath)) {
    Write-Host "[错误] 未找到 SSH 密钥: $KeyPath" -ForegroundColor Red
    exit 1
}

while ($true) {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] 正在建立反向穿透隧道..." -ForegroundColor Cyan
    try {
        & ssh -i $KeyPath `
            -o StrictHostKeyChecking=no `
            -o ExitOnForwardFailure=yes `
            -o ServerAliveInterval=20 `
            -o ServerAliveCountMax=3 `
            -N `
            -R "${RemotePort}:127.0.0.1:${LocalPort}" `
            "${VmUser}@${VmIp}"
        
        $exitCode = $LASTEXITCODE
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] 隧道已断开 (退出码: $exitCode)。将在 5 秒后自动重连..." -ForegroundColor Yellow
    } catch {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] 连接异常: $_" -ForegroundColor Red
    }
    Start-Sleep -Seconds 5
}
