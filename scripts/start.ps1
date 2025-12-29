# Input: 本地 Python/Docker 环境 + config/.env 配置
# Output: 启动 v0 单机栈（infra + worker/collector/api），并写入 var/log 与 .run
# Pos: 运维启动脚本（Windows PowerShell 版；变更时同步更新以上注释与所属目录 FOLDER.md）

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RootDir

$PythonBin = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python" }
$LogDir = if ($env:LOG_DIR) { $env:LOG_DIR } else { "var/log" }
$RunDir = if ($env:RUN_DIR) { $env:RUN_DIR } else { ".run" }
$BootstrapLog = if ($env:BOOTSTRAP_LOG) { $env:BOOTSTRAP_LOG } else { (Join-Path $LogDir "bootstrap.log") }

New-Item -ItemType Directory -Force -Path $LogDir, $RunDir | Out-Null

function Write-Log {
  param([Parameter(Mandatory = $true)][string]$Message)
  $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Write-Host "[$ts] $Message"
}

function Die {
  param([Parameter(Mandatory = $true)][string]$Message)
  Write-Log "ERROR: $Message"
  exit 1
}

function Invoke-Checked {
  param(
    [Parameter(Mandatory = $true)][scriptblock]$ScriptBlock,
    [string]$ErrorMessage = "Command failed"
  )
  try {
    & $ScriptBlock
  } catch {
    Die "$ErrorMessage`n$($_.Exception.Message)"
  }
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

function Wait-Port {
  param(
    [Parameter(Mandatory = $true)][string]$Host,
    [Parameter(Mandatory = $true)][int]$Port,
    [Parameter(Mandatory = $true)][string]$Name,
    [int]$TimeoutSec = 60
  )
  Write-Log "Waiting for $Name on ${Host}:${Port} (timeout ${TimeoutSec}s)..."
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $client = New-Object System.Net.Sockets.TcpClient
      $iar = $client.BeginConnect($Host, $Port, $null, $null)
      if (-not $iar.AsyncWaitHandle.WaitOne(2000, $false)) {
        $client.Close()
        Start-Sleep -Seconds 1
        continue
      }
      $client.EndConnect($iar)
      $client.Close()
      return
    } catch {
      Start-Sleep -Seconds 1
    }
  }
  Die "TIMEOUT waiting for $Name on ${Host}:${Port}"
}

function Invoke-Compose {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
  if (Get-Command docker -ErrorAction SilentlyContinue) {
    & docker compose version *> $null
    if ($LASTEXITCODE -eq 0) {
      & docker compose @Args
      return
    }
  }
  if (Get-Command docker-compose -ErrorAction SilentlyContinue) {
    & docker-compose @Args
    return
  }
  Die "docker compose not found (install Docker Desktop with Compose)"
}

function Import-DotEnv {
  param([Parameter(Mandatory = $true)][string]$Path)
  if (-not (Test-Path $Path)) {
    return
  }
  foreach ($line in (Get-Content -LiteralPath $Path)) {
    $trimmed = $line.Trim()
    if (-not $trimmed) { continue }
    if ($trimmed.StartsWith("#")) { continue }

    $parts = $trimmed -split "=", 2
    if ($parts.Count -ne 2) { continue }

    $key = $parts[0].Trim()
    $value = $parts[1].Trim()
    if (-not $key) { continue }

    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
      $value = $value.Substring(1, $value.Length - 2)
    }
    Set-Item -Path "Env:$key" -Value $value
  }
}

function Start-Bg {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$FilePath,
    [Parameter(Mandatory = $true)][string[]]$ArgumentList,
    [Parameter(Mandatory = $true)][string]$StdoutLog,
    [Parameter(Mandatory = $true)][string]$StderrLog
  )

  $pidFile = Join-Path $RunDir "$Name.pid"
  if (Test-Path $pidFile) {
    $existingPid = (Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($existingPid -and ($existingPid -as [int]) -and (Test-ProcessRunning -Pid ([int]$existingPid))) {
      Write-Log "Already running: $Name (pid=$existingPid)"
      return
    }
    Remove-Item -Force -LiteralPath $pidFile -ErrorAction SilentlyContinue | Out-Null
  }

  Write-Log "Starting $Name..."
  $proc = Start-Process -FilePath $FilePath `
    -ArgumentList $ArgumentList `
    -WorkingDirectory $RootDir `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog `
    -WindowStyle Hidden `
    -PassThru

  Set-Content -LiteralPath $pidFile -Value $proc.Id -NoNewline -Encoding ascii
  Write-Log "Started $Name pid=$($proc.Id) out=$StdoutLog err=$StderrLog"
}

function Stop-All {
  Write-Log "Stopping processes..."
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
  Write-Log "Done."
}

if (-not (Get-Command $PythonBin -ErrorAction SilentlyContinue)) {
  Die "python not found: $PythonBin"
}

if (-not (Test-Path ".venv")) {
  Write-Log "Step 1/8: Creating virtualenv in .venv (this may take a moment)..."
  & $PythonBin -m venv .venv
  if ($LASTEXITCODE -ne 0) { Die "Failed to create virtualenv (exit=$LASTEXITCODE)" }
  Write-Log "Virtualenv created."
} else {
  Write-Log "Step 1/8: Virtualenv already exists (.venv)."
}

$VenvPython = Join-Path $RootDir ".venv\\Scripts\\python.exe"
if (-not (Test-Path $VenvPython)) {
  Die "Virtualenv python not found: $VenvPython"
}

Write-Log "Step 2/8: Upgrading pip..."
(& $VenvPython -m pip install --upgrade pip 2>&1) | Tee-Object -FilePath $BootstrapLog -Append | Out-Host
if ($LASTEXITCODE -ne 0) { Die "pip upgrade failed (exit=$LASTEXITCODE)" }

Write-Log "Step 3/8: Installing Python dependencies (requirements.txt)..."
(& $VenvPython -m pip install -r requirements.txt 2>&1) | Tee-Object -FilePath $BootstrapLog -Append | Out-Host
if ($LASTEXITCODE -ne 0) { Die "pip install -r requirements.txt failed (exit=$LASTEXITCODE)" }

Write-Log "Step 4/8: Installing this repo as editable package (pip install -e .)..."
(& $VenvPython -m pip install -e . 2>&1) | Tee-Object -FilePath $BootstrapLog -Append | Out-Host
if ($LASTEXITCODE -ne 0) { Die "pip install -e . failed (exit=$LASTEXITCODE)" }
Write-Log "Python deps installed. (bootstrap log: $BootstrapLog)"

Write-Log "Sanity check: verifying Celery tasks are registered..."
$taskCheck = @'
from tx_news.tasks.celery_app import celery_app
keys = [k for k in celery_app.tasks.keys() if k.startswith("tx_news.tasks.")]
print(f"Registered tasks: {len(keys)}")
for k in sorted(keys):
    print(" -", k)
if not keys:
    raise SystemExit("ERROR: no tx_news.tasks.* registered; worker would discard tasks")
'@
(& $VenvPython -c $taskCheck 2>&1) | Tee-Object -FilePath $BootstrapLog -Append | Out-Host
if ($LASTEXITCODE -ne 0) { Die "Celery task sanity check failed (exit=$LASTEXITCODE)" }

if (-not (Test-Path ".env")) {
  Write-Log "Creating .env from .env.example ..."
  Copy-Item -LiteralPath ".env.example" -Destination ".env" -Force
}

Import-DotEnv -Path ".env"

Write-Log "Step 5/8: Starting Docker services (postgres/redis/nats/minio/qdrant)..."
Invoke-Compose up -d
if ($LASTEXITCODE -ne 0) { Die "docker compose up failed (exit=$LASTEXITCODE)" }

Wait-Port -Host "127.0.0.1" -Port 5432 -Name "Postgres" -TimeoutSec 90
Wait-Port -Host "127.0.0.1" -Port 6379 -Name "Redis" -TimeoutSec 60
Wait-Port -Host "127.0.0.1" -Port 4222 -Name "NATS" -TimeoutSec 60
Wait-Port -Host "127.0.0.1" -Port 9000 -Name "MinIO" -TimeoutSec 60
Wait-Port -Host "127.0.0.1" -Port 6333 -Name "Qdrant" -TimeoutSec 60
Write-Log "Docker services are ready."

Write-Log "Step 6/8: Optional Tushare A-share master data sync..."
$tushareToken = (& $VenvPython -c @"
import yaml
from pathlib import Path
p=Path('config/config.yaml')
cfg=yaml.safe_load(p.read_text(encoding='utf-8')) if p.exists() else {}
print(((cfg.get('tushare') or {}).get('token') or '').strip())
"@) | Select-Object -First 1

if ($tushareToken) {
  Write-Log "Syncing A-share master data from Tushare..."
  & $VenvPython -m apps.sync_tushare
  if ($LASTEXITCODE -ne 0) { Write-Log "WARN: Tushare sync failed (exit=$LASTEXITCODE; check token/network)." }
} else {
  Write-Log "WARN: tushare.token is empty; skip A-share master data sync."
}

Write-Log "Step 7/8: Starting background processes (Celery worker / NATS bridge / Collector / API)..."
Start-Bg -Name "celery_worker" -FilePath $VenvPython `
  -ArgumentList @("-m","celery","-A","tx_news.tasks.celery_app.celery_app","worker","-l","INFO","--pool=solo","--concurrency=1") `
  -StdoutLog (Join-Path $LogDir "celery_worker.log") `
  -StderrLog (Join-Path $LogDir "celery_worker.err.log")

Start-Bg -Name "nats_bridge" -FilePath $VenvPython `
  -ArgumentList @("-m","apps.worker.nats_bridge") `
  -StdoutLog (Join-Path $LogDir "nats_bridge.log") `
  -StderrLog (Join-Path $LogDir "nats_bridge.err.log")

Start-Bg -Name "collector" -FilePath $VenvPython `
  -ArgumentList @("-m","apps.collector.main") `
  -StdoutLog (Join-Path $LogDir "collector.log") `
  -StderrLog (Join-Path $LogDir "collector.err.log")

Start-Bg -Name "api" -FilePath $VenvPython `
  -ArgumentList @("-m","uvicorn","apps.api.main:app","--host","0.0.0.0","--port","8000") `
  -StdoutLog (Join-Path $LogDir "api.log") `
  -StderrLog (Join-Path $LogDir "api.err.log")

Write-Log "Step 8/8: Startup complete."
Write-Log "Web UI: http://localhost:8000/"
Write-Log "Admin UI: http://localhost:8000/admin"
Write-Log "API: http://localhost:8000 (health: /health, search: /search?q=...)"
Write-Log "Logs: $LogDir/ (bootstrap: $BootstrapLog)"
Write-Log "Stop: Ctrl+C here, or run: scripts\\stop.cmd"
Write-Log "Container services remain running until: docker compose down"

$script:StopRequested = $false
$cancelHandler = [System.ConsoleCancelEventHandler]{
  param($sender, $eventArgs)
  $eventArgs.Cancel = $true
  $script:StopRequested = $true
}
[Console]::add_CancelKeyPress($cancelHandler)
try {
  while (-not $script:StopRequested) {
    Start-Sleep -Seconds 2
  }
} finally {
  [Console]::remove_CancelKeyPress($cancelHandler)
}

Stop-All
