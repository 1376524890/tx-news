# Input: Windows + Docker Desktop(Compose) + .env（可选）+（可选）WSL/Git-Bash（用于宿主机启动 vLLM）
# Output: 通过 docker compose 一键启动 infra + 主程序容器，并做基础健康检查；并输出容器日志到终端与文件
# Pos: 运维启动脚本（Docker 版；变更时同步更新以上注释与所属目录 FOLDER.md）

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RootDir

$LogDir = if ($env:LOG_DIR) { $env:LOG_DIR } else { "var/log" }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$RunDir = if ($env:RUN_DIR) { $env:RUN_DIR } else { ".run" }
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

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
    try {
      $oldPid = (Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1) -as [int]
      if ($oldPid) {
        $null = Get-Process -Id $oldPid -ErrorAction Stop
        Write-Log "Already running: $Name (pid=$oldPid)"
        return
      }
    } catch { }
    Remove-Item -Force -LiteralPath $pidFile -ErrorAction SilentlyContinue | Out-Null
  }
  Write-Log "Starting $Name..."
  $p = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -PassThru -NoNewWindow `
    -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog
  Set-Content -LiteralPath $pidFile -Value $p.Id
  Write-Log "Started $Name pid=$($p.Id) log=$StdoutLog"
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

function Wait-Http {
  param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$Name,
    [int]$TimeoutSec = 60,
    [scriptblock]$OnTick = $null
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
      if ($OnTick) {
        try { & $OnTick } catch { }
      }
      try {
        $resp = $client.GetAsync($Url).GetAwaiter().GetResult()
        if ($resp -and $resp.IsSuccessStatusCode) {
          return $true
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

  return $false
}

if (-not (Test-Path ".env")) {
  Write-Log "Creating .env from .env.example ..."
  Copy-Item -LiteralPath ".env.example" -Destination ".env" -Force
}
Import-DotEnv -Path ".env"

$Accelerator = if ($env:TXNEWS_ACCELERATOR) { "$($env:TXNEWS_ACCELERATOR)".Trim().ToLower() } else { "cpu" }
$StartVllm = if ($env:TXNEWS_START_VLLM) { "$($env:TXNEWS_START_VLLM)".Trim() } else { "1" }
$FollowLogs = if ($env:TXNEWS_FOLLOW_LOGS) { "$($env:TXNEWS_FOLLOW_LOGS)".Trim() } else { "1" }
$ComposeLog = if ($env:TXNEWS_COMPOSE_LOG_FILE) { "$($env:TXNEWS_COMPOSE_LOG_FILE)".Trim() } else { (Join-Path $LogDir "compose.log") }

if (($Accelerator -eq "gpu") -and ($StartVllm -eq "1")) {
  $VllmScript = if ($env:TXNEWS_VLLM_SCRIPT) { "$($env:TXNEWS_VLLM_SCRIPT)".Trim() } else { "finetune/result_model/deepseekr1_merged/serve_vllm_gpu0_9999.sh" }
  $VllmPort = if ($env:TXNEWS_VLLM_PORT) { [int]("$($env:TXNEWS_VLLM_PORT)".Trim()) } else { 9999 }
  $VllmTimeout = if ($env:TXNEWS_VLLM_TIMEOUT_SECONDS) { [int]("$($env:TXNEWS_VLLM_TIMEOUT_SECONDS)".Trim()) } else { 900 }
  $VllmLog = if ($env:TXNEWS_VLLM_LOG_FILE) { "$($env:TXNEWS_VLLM_LOG_FILE)".Trim() } else { (Join-Path $LogDir "vllm.log") }
  $VllmErr = (Join-Path $LogDir "vllm.err.log")

  if (-not (Test-Path $VllmScript)) {
    Die "TXNEWS_ACCELERATOR=gpu but vLLM script not found: $VllmScript"
  }

  Write-Log "Starting host vLLM (outside Docker; preferred for stability)..."
  New-Item -ItemType File -Force -Path $VllmLog | Out-Null
  New-Item -ItemType File -Force -Path $VllmErr | Out-Null
  $bash = Get-Command "bash" -ErrorAction SilentlyContinue
  $wsl = Get-Command "wsl" -ErrorAction SilentlyContinue
  if ($bash) {
    Start-Bg -Name "vllm" -FilePath $bash.Source `
      -ArgumentList @("-lc", "cd '$RootDir' && HOST='0.0.0.0' PORT='$VllmPort' bash '$VllmScript'") `
      -StdoutLog $VllmLog -StderrLog $VllmErr
  } elseif ($wsl) {
    $wslRoot = (& $wsl.Source wslpath -a "$RootDir" 2>$null | Select-Object -First 1)
    if (-not $wslRoot) { Die "WSL is present but failed to resolve repo path via: wsl wslpath -a" }
    $wslScript = (& $wsl.Source wslpath -a (Join-Path $RootDir $VllmScript) 2>$null | Select-Object -First 1)
    if (-not $wslScript) { Die "WSL is present but failed to resolve vLLM script path via: wsl wslpath -a" }
    Start-Bg -Name "vllm" -FilePath $wsl.Source `
      -ArgumentList @("bash", "-lc", "cd '$wslRoot' && HOST='0.0.0.0' PORT='$VllmPort' bash '$wslScript'") `
      -StdoutLog $VllmLog -StderrLog $VllmErr
  } else {
    Die "TXNEWS_ACCELERATOR=gpu requires bash or WSL to run vLLM script on Windows."
  }

  Start-Sleep -Seconds 1
  try {
    $pidFile = Join-Path $RunDir "vllm.pid"
    $pidText = (Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $pid = $pidText -as [int]
    if ($pid) { $null = Get-Process -Id $pid -ErrorAction Stop }
  } catch {
    Write-Log "vLLM process exited early; last logs:"
    try { Get-Content -LiteralPath $VllmLog -Tail 200 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ } } catch { }
    Die "Failed to start vLLM. If you use a venv, set TXNEWS_VLLM_PYTHON to the correct python."
  }

  Write-Log "Tailing vLLM log while waiting -> $VllmLog"
  $tailJob = Start-Job -ScriptBlock {
    param([string]$Path)
    Get-Content -LiteralPath $Path -Tail 200 -Wait
  } -ArgumentList $VllmLog

  $pump = {
    Receive-Job -Job $tailJob -Keep -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
  }

  $ok = $false
  try {
    $ok = Wait-Http -Url "http://127.0.0.1:$VllmPort/v1/models" -Name "vLLM /v1/models" -TimeoutSec $VllmTimeout -OnTick $pump
  } finally {
    try { Stop-Job -Job $tailJob -Force -ErrorAction SilentlyContinue | Out-Null } catch { }
    try { Remove-Job -Job $tailJob -Force -ErrorAction SilentlyContinue | Out-Null } catch { }
  }

  if (-not $ok) {
    Write-Log "vLLM not ready (timeout). Check logs: $VllmLog"
    Die "vLLM not ready (timeout)."
  }
  Write-Log "vLLM is ready."
} else {
  Write-Log "Skip host vLLM (TXNEWS_ACCELERATOR=$Accelerator TXNEWS_START_VLLM=$StartVllm)."
}

Write-Log "Starting containers (profile=app)..."
Invoke-Compose --profile app up -d --build
if ($LASTEXITCODE -ne 0) { Die "docker compose up failed (exit=$LASTEXITCODE)" }

Write-Log "Startup checks..."
if (-not (Wait-Http -Url "http://127.0.0.1:8000/health" -Name "API /health" -TimeoutSec 120)) {
  Invoke-Compose --profile app logs --tail 200 api
  Die "API not ready (timeout)."
}
try {
  $status = Invoke-RestMethod -Uri "http://127.0.0.1:8000/status" -TimeoutSec 5
  if ($status -and $status.counts -and ($status.counts.a_share_basic -as [int]) -eq 0) {
    Write-Log "WARN: a_share_basic is empty (bootstrap may still be running or failed)."
    try { Invoke-Compose --profile app logs --tail 200 bootstrap } catch { }
  }
} catch { }
try { Wait-Http -Url "http://127.0.0.1:8001/health" -Name "Admin /health" -TimeoutSec 120 } catch { }

Write-Log "Startup complete."
Write-Log "Web UI: http://localhost:8000/"
Write-Log "Config UI: http://localhost:8001/"
Write-Log "API: http://localhost:8000 (health: /health, status: /status)"
if ($Accelerator -eq "gpu") {
  $VllmPort = if ($env:TXNEWS_VLLM_PORT) { "$($env:TXNEWS_VLLM_PORT)".Trim() } else { "9999" }
  Write-Log "vLLM: http://localhost:$VllmPort/v1 (models: /v1/models)"
}
Write-Log "Stop: scripts\\stop.cmd"

Write-Log "Streaming docker logs (follow=$FollowLogs) -> $ComposeLog"
if ($FollowLogs -eq "1") {
  try {
    Invoke-Compose --profile app logs --no-color --timestamps -f 2>&1 | Tee-Object -FilePath $ComposeLog -Append
  } catch {
    Invoke-Compose --profile app logs -f 2>&1 | Tee-Object -FilePath $ComposeLog -Append
  }
} else {
  try {
    Invoke-Compose --profile app logs --no-color --timestamps --tail 200 2>&1 | Tee-Object -FilePath $ComposeLog -Append
  } catch {
    Invoke-Compose --profile app logs --tail 200 2>&1 | Tee-Object -FilePath $ComposeLog -Append
  }
}
