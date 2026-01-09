# Input: Windows + PowerShell + Python/Docker + Node.js + config/.env 配置 +（可选）WSL/Git-Bash（用于启动 vLLM）
# Output: 自动安装匹配的 torch、预检 embedding 下载/加载、可选启动 vLLM、本地栈拉起并做启动健康检查
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

function Wait-Http {
  param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$Name,
    [int]$TimeoutSec = 60
  )
  Write-Log "Waiting for $Name HTTP $Url (timeout ${TimeoutSec}s)..."

  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  $lastErr = $null
  $handler = New-Object System.Net.Http.HttpClientHandler
  $handler.AllowAutoRedirect = $true
  $client = New-Object System.Net.Http.HttpClient($handler)
  $client.Timeout = [TimeSpan]::FromSeconds(3)

  try {
    while ((Get-Date) -lt $deadline) {
      try {
        $resp = $client.GetAsync($Url).GetAwaiter().GetResult()
        if ($resp -and $resp.IsSuccessStatusCode) {
          return
        }
        $lastErr = "status=" + [int]$resp.StatusCode
      } catch {
        $lastErr = $_.Exception.Message
      }
      Start-Sleep -Seconds 1
    }
  } finally {
    $client.Dispose()
    $handler.Dispose()
  }

  Die "TIMEOUT waiting for $Name HTTP $Url (last_err=$lastErr)"
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

function Setup-HfEnv {
  if (-not $env:HF_ENDPOINT) {
    $env:HF_ENDPOINT = "https://hf-mirror.com"
  }

  if (-not $env:HF_HOME) {
    $env:HF_HOME = [System.IO.Path]::GetFullPath((Join-Path $RootDir "var/hf"))
  } else {
    # If relative, treat it as relative to repo root (same as bash script).
    $hf = "$($env:HF_HOME)".Trim()
    if ($hf -and -not ([System.IO.Path]::IsPathRooted($hf))) {
      $env:HF_HOME = [System.IO.Path]::GetFullPath((Join-Path $RootDir $hf))
    }
  }
  New-Item -ItemType Directory -Force -Path $env:HF_HOME | Out-Null
}

function Detect-TorchVariant {
  # Output: cpu | cu121 | cu124
  if ($env:TORCH_VARIANT) {
    return "$($env:TORCH_VARIANT)".Trim()
  }

  $nvsmi = Get-Command "nvidia-smi" -ErrorAction SilentlyContinue
  if (-not $nvsmi) {
    return "cpu"
  }

  try {
    $out = & $nvsmi.Source 2>$null | Out-String
    $m = [regex]::Match($out, "CUDA Version:\\s*([0-9.]+)")
    if (-not $m.Success) {
      return "cu121"
    }
    $cuda = $m.Groups[1].Value
    $parts = $cuda.Split(".")
    if ($parts.Count -lt 2) { return "cu121" }
    $major = [int]$parts[0]
    $minor = [int]$parts[1]
    if (($major -gt 12) -or (($major -eq 12) -and ($minor -ge 4))) {
      return "cu124"
    }
    return "cu121"
  } catch {
    return "cu121"
  }
}

function Install-TorchAuto {
  $auto = if ($env:AUTO_TORCH) { "$($env:AUTO_TORCH)".Trim() } else { "1" }
  if ($auto -ne "1") {
    Write-Log "AUTO_TORCH=0; skip torch auto-install."
    return
  }

  $variant = Detect-TorchVariant

  $torchOk = $false
  $torchCuda = ""
  try {
    $torchInfo = & $VenvPython -c "import torch; print(torch.__version__); print(getattr(torch.version,'cuda','') or '')" 2>$null
    if ($LASTEXITCODE -eq 0 -and $torchInfo) {
      $torchOk = $true
      $lines = @($torchInfo)
      if ($lines.Count -ge 2) { $torchCuda = "$($lines[1])".Trim() }
    }
  } catch {
    $torchOk = $false
  }

  if ($torchOk) {
    if ($variant -ne "cpu" -and -not $torchCuda) {
      Write-Log "Torch is installed but CUDA is not enabled; reinstalling PyTorch ($variant)..."
      (& $VenvPython -m pip uninstall -y torch 2>&1) | Tee-Object -FilePath $BootstrapLog -Append | Out-Host
    } else {
      $cudaMsg = if ($torchCuda) { $torchCuda } else { "none" }
      Write-Log "Torch already installed. (variant=$variant cuda=$cudaMsg)"
      return
    }
  }

  $indexUrl = ""
  if ($env:TORCH_INDEX_URL) {
    $indexUrl = "$($env:TORCH_INDEX_URL)".Trim()
  } else {
    switch ($variant) {
      "cpu" { $indexUrl = "https://download.pytorch.org/whl/cpu" }
      "cu121" { $indexUrl = "https://download.pytorch.org/whl/cu121" }
      "cu124" { $indexUrl = "https://download.pytorch.org/whl/cu124" }
      default { Die "invalid TORCH_VARIANT: $variant (expected cpu/cu121/cu124)" }
    }
  }

  Write-Log "Installing PyTorch ($variant) from $indexUrl ..."
  (& $VenvPython -m pip install --upgrade "torch" --index-url $indexUrl 2>&1) | Tee-Object -FilePath $BootstrapLog -Append | Out-Host
  if ($LASTEXITCODE -ne 0) { Die "pip install torch failed (exit=$LASTEXITCODE)" }
}

function Preflight-Embedding {
  $enabled = if ($env:PREFLIGHT_EMBEDDING) { "$($env:PREFLIGHT_EMBEDDING)".Trim() } else { "1" }
  if ($enabled -ne "1") {
    Write-Log "PREFLIGHT_EMBEDDING=0; skip embedding preflight."
    return
  }

  Setup-HfEnv

  Write-Log "Preflight: torch/GPU/HF/embedding..."
  $script = @'
import os
import time

from tx_news.settings import get_settings

settings = get_settings()
file_cfg = settings.load_file_settings()
embedding_cfg = settings.resolve_embedding_cfg(file_cfg)

device_cfg = str(embedding_cfg.get("device", "auto")).strip().lower()
model_name = str(embedding_cfg.get("model_name") or "").strip()

print("HF_ENDPOINT =", os.environ.get("HF_ENDPOINT"))
print("HF_HOME     =", os.environ.get("HF_HOME"))
print("TXNEWS_ACCELERATOR      =", os.environ.get("TXNEWS_ACCELERATOR"))
print("TXNEWS_EMBEDDING_DEVICE =", os.environ.get("TXNEWS_EMBEDDING_DEVICE"))

try:
    import torch

    print("torch       =", torch.__version__)
    print("torch.cuda.is_available =", bool(torch.cuda.is_available()))
    print("torch.version.cuda      =", getattr(torch.version, "cuda", None))
    if torch.cuda.is_available():
        n = torch.cuda.device_count()
        print("cuda.device_count       =", n)
        for i in range(n):
            print(f"cuda[{i}] name          =", torch.cuda.get_device_name(i))
except Exception as e:
    raise SystemExit(f"ERROR: torch import failed: {e}")

if device_cfg in {"auto"} or device_cfg.startswith("cuda"):
    import torch

    if not torch.cuda.is_available():
        raise SystemExit(
            "ERROR: embedding.device expects CUDA, but torch.cuda.is_available() is False. "
            "Fix your torch CUDA build/driver, or set TXNEWS_ACCELERATOR=cpu / TXNEWS_EMBEDDING_DEVICE=cpu."
        )

from tx_news.embedding.embedder import build_embedder

t0 = time.time()
embedder, strategy = build_embedder(embedding_cfg)
vec = embedder.embed("embedding 预检")
dt = time.time() - t0

print("embedding.model_name    =", model_name)
print("embedding.device        =", device_cfg)
print("embedding.dim           =", len(vec))
print("qdrant.strategy         =", strategy)
print(f"embedding.preflight_sec = {dt:.2f}")
'@
  (& $VenvPython -c $script 2>&1) | Tee-Object -FilePath $BootstrapLog -Append | Out-Host
  if ($LASTEXITCODE -ne 0) { Die "Embedding preflight failed (exit=$LASTEXITCODE)" }
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

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  Die "Node.js is required for frontend build. Please install it."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  Die "npm is required for frontend build. Please install it."
}

$Accelerator = if ($env:TXNEWS_ACCELERATOR) { "$($env:TXNEWS_ACCELERATOR)".Trim().ToLowerInvariant() } else { "cpu" }
if (-not $Accelerator) { $Accelerator = "cpu" }

Write-Log "Step 0/12: Building frontend (apps/web)..."
& npm --prefix apps/web install --no-audit --no-fund --silent
if ($LASTEXITCODE -ne 0) { Die "npm install failed (exit=$LASTEXITCODE)" }
& npm --prefix apps/web run build:all
if ($LASTEXITCODE -ne 0) { Die "npm run build:all failed (exit=$LASTEXITCODE)" }

if (-not (Test-Path ".venv")) {
  Write-Log "Step 1/12: Creating virtualenv in .venv (this may take a moment)..."
  & $PythonBin -m venv .venv
  if ($LASTEXITCODE -ne 0) { Die "Failed to create virtualenv (exit=$LASTEXITCODE)" }
  Write-Log "Virtualenv created."
} else {
  Write-Log "Step 1/12: Virtualenv already exists (.venv)."
}

$VenvPython = Join-Path $RootDir ".venv\\Scripts\\python.exe"
if (-not (Test-Path $VenvPython)) {
  Die "Virtualenv python not found: $VenvPython"
}

Write-Log "Step 2/12: Upgrading pip..."
(& $VenvPython -m pip install --upgrade pip 2>&1) | Tee-Object -FilePath $BootstrapLog -Append | Out-Host
if ($LASTEXITCODE -ne 0) { Die "pip upgrade failed (exit=$LASTEXITCODE)" }

Write-Log "Step 3/12: Auto-installing torch (CPU/CUDA)..."
Install-TorchAuto

Write-Log "Step 4/12: Installing Python dependencies (requirements.txt)..."
(& $VenvPython -m pip install -r requirements.txt 2>&1) | Tee-Object -FilePath $BootstrapLog -Append | Out-Host
if ($LASTEXITCODE -ne 0) { Die "pip install -r requirements.txt failed (exit=$LASTEXITCODE)" }

Write-Log "Step 5/12: Installing this repo as editable package (pip install -e .)..."
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

Write-Log "Step 6/12: Preflight embedding (HF mirror + model load)..."
Preflight-Embedding

Write-Log "Step 7/12: Starting Docker services (postgres/redis/nats/minio/qdrant)..."
Invoke-Compose up -d
if ($LASTEXITCODE -ne 0) { Die "docker compose up failed (exit=$LASTEXITCODE)" }

Wait-Port -Host "127.0.0.1" -Port 5432 -Name "Postgres" -TimeoutSec 90
Wait-Port -Host "127.0.0.1" -Port 6379 -Name "Redis" -TimeoutSec 60
Wait-Port -Host "127.0.0.1" -Port 4222 -Name "NATS" -TimeoutSec 60
Wait-Port -Host "127.0.0.1" -Port 9000 -Name "MinIO" -TimeoutSec 60
Wait-Port -Host "127.0.0.1" -Port 6333 -Name "Qdrant" -TimeoutSec 60
Write-Log "Docker services are ready."

Write-Log "Step 8/12: Optional Tushare A-share master data sync..."
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

if ($Accelerator -eq "gpu") {
  Write-Log "Step 9/12: Starting vLLM for deep analysis..."
  $VllmScript = if ($env:TXNEWS_VLLM_SCRIPT) { "$($env:TXNEWS_VLLM_SCRIPT)".Trim() } else { "finetune/result_model/deepseekr1_merged/serve_vllm_gpu0_9999.sh" }
  $VllmPort = if ($env:TXNEWS_VLLM_PORT) { [int]("$($env:TXNEWS_VLLM_PORT)".Trim()) } else { 9999 }
  $VllmTimeout = if ($env:TXNEWS_VLLM_TIMEOUT_SECONDS) { [int]("$($env:TXNEWS_VLLM_TIMEOUT_SECONDS)".Trim()) } else { 600 }

  if (-not (Test-Path $VllmScript)) {
    Die "TXNEWS_ACCELERATOR=gpu but vLLM script not found: $VllmScript"
  }

  $bash = Get-Command "bash" -ErrorAction SilentlyContinue
  $wsl = Get-Command "wsl" -ErrorAction SilentlyContinue

  if ($bash) {
    Start-Bg -Name "vllm" -FilePath $bash.Source `
      -ArgumentList @("-lc", "cd '$RootDir' && PORT='$VllmPort' bash '$VllmScript'") `
      -StdoutLog (Join-Path $LogDir "vllm.log") `
      -StderrLog (Join-Path $LogDir "vllm.err.log")
    Wait-Http -Url "http://127.0.0.1:$VllmPort/v1/models" -Name "vLLM" -TimeoutSec $VllmTimeout
    Write-Log "vLLM is ready."
  } elseif ($wsl) {
    $wslRoot = (& $wsl.Source wslpath -a "$RootDir" 2>$null | Select-Object -First 1)
    if (-not $wslRoot) { Die "WSL is present but failed to resolve repo path via: wsl wslpath -a" }
    $wslScript = (& $wsl.Source wslpath -a (Join-Path $RootDir $VllmScript) 2>$null | Select-Object -First 1)
    if (-not $wslScript) { Die "WSL is present but failed to resolve vLLM script path via: wsl wslpath -a" }

    Start-Bg -Name "vllm" -FilePath $wsl.Source `
      -ArgumentList @("bash", "-lc", "cd '$wslRoot' && PORT='$VllmPort' bash '$wslScript'") `
      -StdoutLog (Join-Path $LogDir "vllm.log") `
      -StderrLog (Join-Path $LogDir "vllm.err.log")
    Wait-Http -Url "http://127.0.0.1:$VllmPort/v1/models" -Name "vLLM" -TimeoutSec $VllmTimeout
    Write-Log "vLLM is ready."
  } else {
    Die "TXNEWS_ACCELERATOR=gpu requires bash or WSL to run vLLM script on Windows. Install WSL or Git-Bash."
  }
} else {
  Write-Log "Step 9/12: Skip vLLM (TXNEWS_ACCELERATOR=$Accelerator)."
}

Write-Log "Step 10/12: Starting background processes (Celery worker / NATS bridge / Collector / API / Config)..."
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

Start-Bg -Name "config" -FilePath $VenvPython `
  -ArgumentList @("-m","uvicorn","apps.admin.main:app","--host","0.0.0.0","--port","8001") `
  -StdoutLog (Join-Path $LogDir "config.log") `
  -StderrLog (Join-Path $LogDir "config.err.log")

Write-Log "Step 11/12: Startup checks..."
Wait-Http -Url "http://127.0.0.1:8000/health" -Name "API /health" -TimeoutSec 60
Wait-Http -Url "http://127.0.0.1:8001/health" -Name "Config /health" -TimeoutSec 60
if ($Accelerator -eq "gpu") {
  $VllmPort = if ($env:TXNEWS_VLLM_PORT) { [int]("$($env:TXNEWS_VLLM_PORT)".Trim()) } else { 9999 }
  Wait-Http -Url "http://127.0.0.1:$VllmPort/v1/models" -Name "vLLM /v1/models" -TimeoutSec 10
}
Write-Log "Startup checks passed."

Write-Log "Step 12/12: Startup complete."
Write-Log "Web UI: http://localhost:8000/"
Write-Log "Config UI: http://localhost:8001/"
Write-Log "API: http://localhost:8000 (health: /health, search: /search?q=...)"
if ($Accelerator -eq "gpu") {
  $VllmPort = if ($env:TXNEWS_VLLM_PORT) { "$($env:TXNEWS_VLLM_PORT)".Trim() } else { "9999" }
  Write-Log "vLLM: http://localhost:$VllmPort/v1 (models: /v1/models)"
}
Write-Log "Logs: $LogDir/ (bootstrap: $BootstrapLog)"
Write-Log "Stop: Ctrl+C here, or run: scripts\stop.cmd"
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
