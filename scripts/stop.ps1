# Input: docker compose 编排 +（可选）.env/.run/vllm.pid
# Output: 停止本仓库 docker compose 全部服务，并尽力停止宿主机 vLLM（pidfile/端口探测）
# Pos: 运维停止脚本（Docker 版；变更时同步更新以上注释与所属目录 FOLDER.md）

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

function Die {
  param([Parameter(Mandatory = $true)][string]$Message)
  Write-Log "ERROR: $Message"
  exit 1
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

function Stop-ProcessTree {
  param([Parameter(Mandatory = $true)][int]$Pid)
  try {
    $p = Get-Process -Id $Pid -ErrorAction Stop
  } catch {
    return
  }
  Write-Log "Killing pid=$Pid name=$($p.ProcessName)"
  try { Stop-Process -Id $Pid -Force -ErrorAction SilentlyContinue } catch { }
}

Import-DotEnv -Path ".env"
$VllmPort = if ($env:TXNEWS_VLLM_PORT) { [int]("$($env:TXNEWS_VLLM_PORT)".Trim()) } else { 9999 }

try {
  $pidFile = Join-Path $RunDir "vllm.pid"
  if (Test-Path $pidFile) {
    $pidText = (Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $pid = $pidText -as [int]
    if ($pid) {
      try {
        $null = Get-Process -Id $pid -ErrorAction Stop
        Write-Log "Killing host vLLM pid=$pid (from $pidFile)"
        Stop-ProcessTree -Pid $pid
      } catch { }
    }
    Remove-Item -Force -LiteralPath $pidFile -ErrorAction SilentlyContinue | Out-Null
  }
} catch { }

try {
  $conns = Get-NetTCPConnection -State Listen -LocalPort $VllmPort -ErrorAction SilentlyContinue
  foreach ($c in $conns) {
    $pid = [int]$c.OwningProcess
    if (-not $pid) { continue }
    try {
      $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$pid" -ErrorAction SilentlyContinue).CommandLine
      if ($cmd -and ($cmd -match "vllm\.entrypoints\.openai\.api_server" -or $cmd -match "\\bvllm\\b" -or $cmd -match "api_server")) {
        Write-Log "Killing vLLM on port $VllmPort pid=$pid cmd=$cmd"
        Stop-ProcessTree -Pid $pid
      } else {
        Write-Log "Port $VllmPort is used by pid=$pid (not vLLM); skip."
      }
    } catch { }
  }
} catch { }

Invoke-Compose down
Write-Host "Stopped. (Data volumes are preserved; use: docker compose down -v)"
