# Input: .run/*.pid（由 scripts/start.ps1 生成）
# Output: 停止本地后台进程（不自动停止 Docker services）
# Pos: 运维停止脚本（Windows PowerShell 版；变更时同步更新以上注释与所属目录 FOLDER.md）

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RootDir

$RunDir = if ($env:RUN_DIR) { $env:RUN_DIR } else { ".run" }

function Write-Log {
  param([Parameter(Mandatory = $true)][string]$Message)
  $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Write-Host "[$ts] $Message"
}

function Test-ProcessRunning {
  param([Parameter(Mandatory = $true)][int]$Pid)
  try {
    $null = Get-Process -Id $Pid -ErrorAction Stop
    return $true
  } catch {
    return $false
  }
}

foreach ($pidFile in (Get-ChildItem -LiteralPath $RunDir -Filter "*.pid" -ErrorAction SilentlyContinue)) {
  $pidText = (Get-Content -LiteralPath $pidFile.FullName -ErrorAction SilentlyContinue | Select-Object -First 1)
  $name = [System.IO.Path]::GetFileNameWithoutExtension($pidFile.Name)
  if (-not $pidText) {
    Remove-Item -Force -LiteralPath $pidFile.FullName -ErrorAction SilentlyContinue | Out-Null
    continue
  }

  $pid = $pidText -as [int]
  if (-not $pid) {
    Remove-Item -Force -LiteralPath $pidFile.FullName -ErrorAction SilentlyContinue | Out-Null
    continue
  }

  if (Test-ProcessRunning -Pid $pid) {
    Write-Log "Killing $name pid=$pid"
    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
  }
  Remove-Item -Force -LiteralPath $pidFile.FullName -ErrorAction SilentlyContinue | Out-Null
}

Write-Host "Stopped. (Docker services are still running; use: docker compose down)"

