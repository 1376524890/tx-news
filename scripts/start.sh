#!/usr/bin/env bash
# Input: Docker + Docker Compose + config/.env（可选）+（可选）宿主机 vLLM 脚本
# Output: 通过 docker compose 一键启动 infra + 主程序容器，并做基础健康检查；并输出容器日志到终端与文件
# Pos: 运维启动脚本（Docker 版；变更时同步更新以上注释与所属目录 FOLDER.md）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

LOG_DIR="${LOG_DIR:-var/log}"
RUN_DIR="${RUN_DIR:-.run}"
mkdir -p "${LOG_DIR}"
mkdir -p "${RUN_DIR}"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
  log "ERROR: $*" >&2
  exit 1
}

have() {
  command -v "$1" >/dev/null 2>&1
}

docker_accessible() {
  if have docker && docker info >/dev/null 2>&1; then
    return 0
  fi
  if have sudo && sudo -n docker info >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

compose() {
  if have docker; then
    if docker info >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
      docker compose "$@"
      return
    fi
    if have sudo && sudo -n docker info >/dev/null 2>&1 && sudo -n docker compose version >/dev/null 2>&1; then
      sudo docker compose "$@"
      return
    fi
  fi
  if have docker-compose; then
    docker-compose "$@"
    return
  fi
  if have sudo && have docker-compose && sudo -n docker-compose version >/dev/null 2>&1; then
    sudo docker-compose "$@"
    return
  fi
  die "docker compose not available (install Docker + Compose; or run with sudo / add user to docker group)"
}

wait_http() {
  local url="$1"
  local name="$2"
  local timeout="${3:-60}"
  log "Waiting for ${name} HTTP ${url} (timeout ${timeout}s)..."
  local deadline
  deadline="$(( $(date +%s) + timeout ))"
  while [[ "$(date +%s)" -lt "${deadline}" ]]; do
    if have curl; then
      if curl -fsS --max-time 3 "${url}" >/dev/null 2>&1; then
        return 0
      fi
    elif have wget; then
      if wget -q -T 3 -O /dev/null "${url}" >/dev/null 2>&1; then
        return 0
      fi
    else
      die "need curl or wget for health checks"
    fi
    sleep 1
  done
  return 1
}

stop_pid() {
  local pid="${1:-}"
  [[ -n "${pid}" ]] || return 0
  # If the pid is a process group leader (e.g. started via setsid), try killing the whole group first.
  kill -- -"${pid}" >/dev/null 2>&1 || true
  kill "${pid}" >/dev/null 2>&1 || true
}

start_bg() {
  local name="$1"
  local cmd="$2"
  local logfile="$3"
  local pidfile="${RUN_DIR}/${name}.pid"

  if [[ -f "${pidfile}" ]]; then
    local pid
    pid="$(cat "${pidfile}" || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
      log "Already running: ${name} (pid=${pid})"
      return 0
    fi
    rm -f "${pidfile}" || true
  fi

  log "Starting ${name}..."
  if have setsid; then
    (setsid bash -lc "${cmd}" >>"${logfile}" 2>&1) &
  else
    (bash -lc "${cmd}" >>"${logfile}" 2>&1) &
  fi
  echo $! > "${pidfile}"
  log "Started ${name} pid=$(cat "${pidfile}") log=${logfile}"
}

TAIL_PID=""
start_tail_file() {
  local path="$1"
  local tail_lines="${2:-200}"
  TAIL_PID=""
  if ! have tail; then
    return 0
  fi
  (
    set +e
    # -F: follow by name (handles file creation/rotation); fallback to -f if unsupported.
    tail -n "${tail_lines}" -F "${path}" 2>/dev/null || tail -n "${tail_lines}" -f "${path}"
  ) &
  TAIL_PID="$!"
}

compose_logs() {
  local follow="$1"
  local outfile="$2"
  local tail="${3:-200}"

  if [[ "${follow}" == "1" ]]; then
    if ! compose --profile app logs --no-color --timestamps -f 2>&1 | tee -a "${outfile}"; then
      compose --profile app logs -f 2>&1 | tee -a "${outfile}"
    fi
  else
    if ! compose --profile app logs --no-color --timestamps --tail "${tail}" 2>&1 | tee -a "${outfile}"; then
      compose --profile app logs --tail "${tail}" 2>&1 | tee -a "${outfile}"
    fi
  fi
}

if [[ ! -f ".env" ]]; then
  log "Creating .env from .env.example ..."
  cp .env.example .env
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

START_VLLM="$(printf '%s' "${TXNEWS_START_VLLM:-1}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
ACCEL="$(printf '%s' "${TXNEWS_ACCELERATOR:-cpu}" | tr '[:upper:]' '[:lower:]' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
FOLLOW_LOGS="${TXNEWS_FOLLOW_LOGS:-1}"
COMPOSE_LOG_FILE="${TXNEWS_COMPOSE_LOG_FILE:-${LOG_DIR}/compose.log}"
VLLM_LOG_FILE="${TXNEWS_VLLM_LOG_FILE:-${LOG_DIR}/vllm.log}"

if [[ "${ACCEL}" == "gpu" && "${START_VLLM}" == "1" ]]; then
  log "Starting host vLLM (outside Docker; preferred for stability)..."
  VLLM_SCRIPT="${TXNEWS_VLLM_SCRIPT:-finetune/result_model/deepseekr1_merged/serve_vllm_gpu0_9999.sh}"
  VLLM_PORT="${TXNEWS_VLLM_PORT:-9999}"
  VLLM_TIMEOUT="${TXNEWS_VLLM_TIMEOUT_SECONDS:-900}"
  if [[ ! -f "${VLLM_SCRIPT}" ]]; then
    die "TXNEWS_ACCELERATOR=gpu but vLLM script not found: ${VLLM_SCRIPT}"
  fi

  start_bg "vllm" "cd '${ROOT_DIR}' && HOST='0.0.0.0' PORT='${VLLM_PORT}' bash '${VLLM_SCRIPT}'" "${VLLM_LOG_FILE}"
  sleep 1
  vllm_pid="$(cat "${RUN_DIR}/vllm.pid" 2>/dev/null || true)"
  if [[ -n "${vllm_pid}" ]] && ! kill -0 "${vllm_pid}" >/dev/null 2>&1; then
    log "vLLM process exited early; last logs:"
    tail -n 200 "${VLLM_LOG_FILE}" 2>/dev/null || true
    die "Failed to start vLLM. If you use a venv, set TXNEWS_VLLM_PYTHON to the correct python."
  fi
  log "Tailing vLLM log while waiting -> ${VLLM_LOG_FILE}"
  start_tail_file "${VLLM_LOG_FILE}" 200
  vllm_tail_pid="${TAIL_PID}"
  trap 'stop_pid "${vllm_tail_pid:-}"; exit 1' INT TERM
  if ! wait_http "http://127.0.0.1:${VLLM_PORT}/v1/models" "vLLM /v1/models" "${VLLM_TIMEOUT}"; then
    stop_pid "${vllm_tail_pid:-}"
    die "vLLM not ready (timeout). Check ${VLLM_LOG_FILE}."
  fi
  stop_pid "${vllm_tail_pid:-}"
  log "vLLM is ready. (containers use TXNEWS_LLM_DEEP_BASE_URL=${TXNEWS_LLM_DEEP_BASE_URL:-http://host.docker.internal:${VLLM_PORT}/v1})"
else
  log "Skip host vLLM (TXNEWS_ACCELERATOR=${ACCEL} TXNEWS_START_VLLM=${START_VLLM})."
fi

log "Starting containers (profile=app)..."
if ! docker_accessible; then
  die "Docker daemon is not accessible (cannot connect to /var/run/docker.sock). Run with sudo / fix docker permissions, then retry."
fi
compose --profile app up -d --build

log "Startup checks..."
if ! wait_http "http://127.0.0.1:8000/health" "API /health" 120; then
  compose --profile app logs --tail 200 api || true
  die "API not ready (timeout)."
fi
if have curl; then
  if curl -fsS --max-time 5 "http://127.0.0.1:8000/status" 2>/dev/null | grep -Eq '"a_share_basic"[[:space:]]*:[[:space:]]*0'; then
    log "WARN: a_share_basic is empty (bootstrap may still be running or failed)."
    compose --profile app logs --tail 200 bootstrap || true
  fi
fi
wait_http "http://127.0.0.1:8001/health" "Admin /health" 120 || true
if [[ "${ACCEL}" == "gpu" ]]; then
  VLLM_PORT="${TXNEWS_VLLM_PORT:-9999}"
  wait_http "http://127.0.0.1:${VLLM_PORT}/v1/models" "vLLM /v1/models" 10 || true
fi

log "Startup complete."
log "Web UI: http://localhost:8000/"
log "Config UI: http://localhost:8001/"
log "API: http://localhost:8000 (health: /health, search: /search?q=..., status: /status)"
if [[ "${ACCEL}" == "gpu" ]]; then
  log "vLLM: http://localhost:${TXNEWS_VLLM_PORT:-9999}/v1 (models: /v1/models)"
fi
log "Streaming docker logs (follow=${FOLLOW_LOGS}) -> ${COMPOSE_LOG_FILE}"
log "Stop containers: bash scripts/stop.sh"
compose_logs "${FOLLOW_LOGS}" "${COMPOSE_LOG_FILE}"
