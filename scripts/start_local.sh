#!/usr/bin/env bash
# Input: 本地 Python(v>=3.10) + venv + Docker(仅 infra) + .env/config/* +（可选）宿主机 vLLM + Node/npm
# Output: infra 使用 docker compose；主程序使用 venv 在宿主机启动（api/worker/collector/nats-bridge/beat/admin + db-init/bootstrap）并做就绪检查
# Pos: 运维入口（本地非 Docker 主程序一键启动）（变更时同步更新以上注释与所属目录 FOLDER.md）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
  log "ERROR: $*" >&2
  exit 1
}

have() { command -v "$1" >/dev/null 2>&1; }

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

compose_available() {
  if have docker; then
    if docker info >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
      return 0
    fi
    if have sudo && sudo -n docker info >/dev/null 2>&1 && sudo -n docker compose version >/dev/null 2>&1; then
      return 0
    fi
  fi
  if have docker-compose; then
    return 0
  fi
  if have sudo && have docker-compose && sudo -n docker-compose version >/dev/null 2>&1; then
    return 0
  fi
  return 1
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

usage() {
  cat <<'EOF'
Usage:
  bash scripts/start_local.sh [--with-admin] [--no-admin] [--reload] [--skip-infra] [--skip-install]
                           [--web-dev] [--no-web-dev] [--web-dev-mode public|admin] [--skip-web-install]

What it does:
  - Starts infra only via docker compose (postgres/redis/nats/minio/qdrant)
  - Starts app processes on the host using a venv:
      - API (uvicorn, :8000)
      - Celery worker
      - Celery beat
      - NATS bridge
      - Collector
      - Admin/config UI (:8001; use --no-admin to skip)
  - If venv is missing, it will be created automatically; dependencies are installed via pip by default.
  - Runs db-init/bootstrap once (mirrors docker compose profile=app behavior).
  - By default starts Vite dev server for the web UI (hot reload) on the external port (default 8000).
    In this mode the API is moved to TXNEWS_LOCAL_API_PORT (default 18000) so the browser only needs port 8000.
  - GPU mode mirrors scripts/start.sh: TXNEWS_ACCELERATOR=gpu + TXNEWS_START_VLLM=1 starts host vLLM and waits ready.

Env knobs:
  - TXNEWS_VENV_DIR (default: .venv)
  - TXNEWS_VENV_PYTHON (default: python3, fallback: python)
  - TXNEWS_LOCAL_LOG_DIR (default: var/log/local)
  - TXNEWS_LOCAL_RUN_DIR (default: .run/local)
  - TXNEWS_WEB_DEV_PORT (default: 8000)
  - TXNEWS_LOCAL_API_PORT (default: 18000; only used when WEB dev is enabled)
  - TXNEWS_SKIP_WEB_INSTALL (default: 0; set to 1 to skip npm install)
EOF
}

WITH_ADMIN="1"
RELOAD="0"
SKIP_INFRA="0"
SKIP_INSTALL_DEPS="0"
SKIP_WEB_INSTALL_DEPS="0"
WEB_DEV="1"
WEB_DEV_MODE="public"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-admin) WITH_ADMIN="1"; shift ;;
    --no-admin) WITH_ADMIN="0"; shift ;;
    --reload) RELOAD="1"; shift ;;
    --skip-infra) SKIP_INFRA="1"; shift ;;
    --skip-install) SKIP_INSTALL_DEPS="1"; shift ;;
    --skip-web-install) SKIP_WEB_INSTALL_DEPS="1"; shift ;;
    --web-dev) WEB_DEV="1"; shift ;;
    --no-web-dev) WEB_DEV="0"; shift ;;
    --web-dev-mode) WEB_DEV_MODE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ ! -f ".env" ]]; then
  log "Creating .env from .env.example ..."
  cp .env.example .env
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

if [[ "${SKIP_WEB_INSTALL_DEPS}" == "0" && "${TXNEWS_SKIP_WEB_INSTALL:-0}" == "1" ]]; then
  SKIP_WEB_INSTALL_DEPS="1"
fi

VENV_DIR="${TXNEWS_VENV_DIR:-.venv}"
VENV_PYTHON="${TXNEWS_VENV_PYTHON:-python3}"
LOG_DIR="${TXNEWS_LOCAL_LOG_DIR:-var/log/local}"
RUN_DIR="${TXNEWS_LOCAL_RUN_DIR:-.run/local}"
WEB_DEV_PORT="${TXNEWS_WEB_DEV_PORT:-8000}"
API_PORT="${TXNEWS_API_PORT:-8000}"
ADMIN_PORT="${TXNEWS_ADMIN_PORT:-8001}"
LOCAL_API_PORT="${TXNEWS_LOCAL_API_PORT:-18000}"

mkdir -p "${LOG_DIR}" "${RUN_DIR}"

LOCAL_ENV_TOUCHED="0"
normalize_env_host() {
  local name="$1"
  local match="$2"
  local replace="$3"
  local default_value="$4"
  local value="${!name:-}"

  if [[ -z "${value}" ]]; then
    export "${name}=${default_value}"
    LOCAL_ENV_TOUCHED="1"
    return 0
  fi
  if [[ "${value}" == *"${match}"* ]]; then
    export "${name}=${value/${match}/${replace}}"
    LOCAL_ENV_TOUCHED="1"
  fi
}

normalize_env_host TXNEWS_PG_DSN "@postgres:" "@localhost:" "postgresql+psycopg://txnews:txnews@localhost:5432/txnews"
normalize_env_host TXNEWS_REDIS_URL "redis://redis:" "redis://localhost:" "redis://localhost:6379/0"
normalize_env_host TXNEWS_NATS_URL "nats://nats:" "nats://localhost:" "nats://localhost:4222"
normalize_env_host TXNEWS_S3_ENDPOINT "://minio:" "://localhost:" "http://localhost:9000"
normalize_env_host TXNEWS_QDRANT_URL "://qdrant:" "://localhost:" "http://localhost:6333"

if [[ -n "${TXNEWS_LLM_DEEP_BASE_URL:-}" && "${TXNEWS_LLM_DEEP_BASE_URL}" == *"host.docker.internal"* ]]; then
  TXNEWS_LLM_DEEP_BASE_URL="${TXNEWS_LLM_DEEP_BASE_URL/host.docker.internal/127.0.0.1}"
  export TXNEWS_LLM_DEEP_BASE_URL
  LOCAL_ENV_TOUCHED="1"
fi

if [[ "${LOCAL_ENV_TOUCHED}" == "1" ]]; then
  log "Local infra env normalized to localhost for host processes."
fi

START_VLLM="$(printf '%s' "${TXNEWS_START_VLLM:-1}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
ACCEL="$(printf '%s' "${TXNEWS_ACCELERATOR:-cpu}" | tr '[:upper:]' '[:lower:]' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"

port_is_listening() {
  local port="$1"
  if have ss; then
    if ss -ltn "( sport = :${port} )" 2>/dev/null | tail -n +2 | grep -q .; then
      return 0
    fi
    return 1
  fi
  if have lsof; then
    lsof -n -P -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  # No diagnostics tool; assume not listening.
  return 1
}

port_diag() {
  local port="$1"
  echo "Port ${port} appears to be in use. Details:"
  if have ss; then
    ss -ltnp "( sport = :${port} )" 2>/dev/null || true
  elif have lsof; then
    lsof -n -P -iTCP:"${port}" -sTCP:LISTEN || true
  else
    echo "Install ss (iproute2) or lsof for port diagnostics."
  fi
}

stop_pidfile() {
  local pid_file="$1"
  [[ -f "${pid_file}" ]] || return 0
  local pid
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ -z "${pid}" ]]; then
    rm -f "${pid_file}"
    return 0
  fi
  if ! kill -0 "${pid}" >/dev/null 2>&1; then
    rm -f "${pid_file}"
    return 0
  fi
  kill "${pid}" >/dev/null 2>&1 || true
  for _ in $(seq 1 30); do
    if ! kill -0 "${pid}" >/dev/null 2>&1; then
      rm -f "${pid_file}"
      return 0
    fi
    sleep 0.2
  done
  kill -9 "${pid}" >/dev/null 2>&1 || true
  rm -f "${pid_file}"
}

log "== Cleanup previous local processes (best-effort) =="
shopt -s nullglob
for f in "${RUN_DIR}"/*.pid; do
  stop_pidfile "${f}"
done

if [[ "${SKIP_INFRA}" != "1" ]]; then
  log "== Start infra (docker compose) =="
  if ! docker_accessible; then
    die "Docker daemon is not accessible (cannot connect to /var/run/docker.sock). Run with sudo / fix docker permissions, then retry."
  fi
  compose up -d
  echo "compose_up=1" > "${RUN_DIR}/infra.started"
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  if ! have "${VENV_PYTHON}"; then
    if have python; then
      VENV_PYTHON="python"
    else
      echo "Missing python interpreter. Set TXNEWS_VENV_PYTHON or install python>=3.10." >&2
      exit 2
    fi
  fi
  echo "== Create venv (${VENV_DIR}) =="
  "${VENV_PYTHON}" -c 'import sys; assert sys.version_info >= (3, 10), sys.version' || {
    echo "Need python>=3.10 for this project." >&2
    exit 2
  }
  "${VENV_PYTHON}" -m venv "${VENV_DIR}"
fi

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "Invalid venv: ${VENV_DIR} (missing ${VENV_DIR}/bin/python)." >&2
  echo "If this path is not a venv, remove it and re-run." >&2
  exit 2
fi

# Activate venv for consistent 'python/pip' resolution.
# shellcheck disable=SC1090
source "${VENV_DIR}/bin/activate"

if [[ "${SKIP_INSTALL_DEPS}" != "1" ]]; then
  echo "== Install deps (requirements.txt) =="
  pip install -r requirements.txt
fi

export PYTHONPATH="${ROOT_DIR}/src"
export PYTHONUNBUFFERED=1

is_running() {
  local pid_file="$1"
  [[ -f "${pid_file}" ]] || return 1
  local pid
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  [[ -n "${pid}" ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1
}

kill_cmd_pids() {
  local pids=("$@")
  for pid in "${pids[@]}"; do
    [[ -n "${pid}" ]] || continue
    if kill -0 "${pid}" >/dev/null 2>&1; then
      kill "${pid}" >/dev/null 2>&1 || true
    fi
  done
}

kill_project_listeners_on_port() {
  local port="$1"
  # Kill known tx-news dev servers that advertise the port in their argv.
  # This is intentionally conservative: it won't kill arbitrary listeners like nginx.
  local pids=()
  while IFS= read -r line; do
    # shellcheck disable=SC2206
    parts=($line)
    pid="${parts[0]}"
    cmd="${line#${pid} }"
    if [[ "${cmd}" == *"--port ${port}"* ]]; then
      if [[ "${cmd}" == *"apps.api.main:app"* || "${cmd}" == *"apps.admin.main:app"* ]]; then
        pids+=("${pid}")
        continue
      fi
      if [[ "${cmd}" == *"${ROOT_DIR}/apps/web/node_modules/.bin/vite"* || "${cmd}" == *" vite "* ]]; then
        pids+=("${pid}")
        continue
      fi
      if [[ "${cmd}" == *"${ROOT_DIR}"* && ( "${cmd}" == *"uvicorn"* || "${cmd}" == *"vite"* ) ]]; then
        pids+=("${pid}")
        continue
      fi
    fi
  done < <(ps -eo pid=,args=)

  if [[ "${#pids[@]}" -gt 0 ]]; then
    echo "free_port: killing tx-news listeners on :${port} pids=${pids[*]}"
    kill_cmd_pids "${pids[@]}"
    sleep 0.3
  fi
}

port_diag_ps() {
  local port="$1"
  echo "Port ${port} may be in use. Matching processes (best-effort):"
  ps -eo pid=,args= | grep -F " --port ${port}" || true
}

pick_free_port() {
  local base="$1"
  local max_tries="${2:-50}"
  for i in $(seq 0 "${max_tries}"); do
    local p=$((base + i))
    if ! port_is_listening "${p}"; then
      echo "${p}"
      return 0
    fi
  done
  return 1
}

start_bg() {
  local name="$1"; shift
  local pid_file="${RUN_DIR}/${name}.pid"
  local log_file="${LOG_DIR}/${name}.log"

  if is_running "${pid_file}"; then
    echo "skip: ${name} already running (pid=$(cat "${pid_file}"))"
    return 0
  fi

  echo "start: ${name} -> ${log_file}"
  # Start in background and record PID.
  nohup "$@" >>"${log_file}" 2>&1 &
  echo $! >"${pid_file}"
}

start_bg_with_log() {
  local name="$1"
  local log_file="$2"
  shift 2
  local pid_file="${RUN_DIR}/${name}.pid"

  if is_running "${pid_file}"; then
    echo "skip: ${name} already running (pid=$(cat "${pid_file}"))"
    return 0
  fi

  echo "start: ${name} -> ${log_file}"
  nohup "$@" >>"${log_file}" 2>&1 &
  echo $! >"${pid_file}"
}

start_bg_env() {
  local name="$1"; shift
  local pid_file="${RUN_DIR}/${name}.pid"
  local log_file="${LOG_DIR}/${name}.log"

  if is_running "${pid_file}"; then
    echo "skip: ${name} already running (pid=$(cat "${pid_file}"))"
    return 0
  fi

  echo "start: ${name} -> ${log_file}"
  nohup env "$@" >>"${log_file}" 2>&1 &
  echo $! >"${pid_file}"
}

UVICORN_RELOAD_ARGS=()
if [[ "${RELOAD}" == "1" ]]; then
  UVICORN_RELOAD_ARGS+=(--reload)
fi

VLLM_PORT="${TXNEWS_VLLM_PORT:-9999}"
VLLM_STARTED="0"
if [[ "${ACCEL}" == "gpu" && "${START_VLLM}" == "1" ]]; then
  log "Starting host vLLM (outside Docker; preferred for stability)..."
  VLLM_SCRIPT="${TXNEWS_VLLM_SCRIPT:-finetune/result_model/deepseekr1_merged/serve_vllm_gpu0_9999.sh}"
  VLLM_TIMEOUT="${TXNEWS_VLLM_TIMEOUT_SECONDS:-900}"
  VLLM_LOG_FILE="${TXNEWS_VLLM_LOG_FILE:-${LOG_DIR}/vllm.log}"
  mkdir -p "$(dirname "${VLLM_LOG_FILE}")"

  if [[ ! -f "${VLLM_SCRIPT}" ]]; then
    die "TXNEWS_ACCELERATOR=gpu but vLLM script not found: ${VLLM_SCRIPT}"
  fi

  start_bg_with_log vllm "${VLLM_LOG_FILE}" bash -lc \
    "cd '${ROOT_DIR}' && HOST='0.0.0.0' PORT='${VLLM_PORT}' bash '${VLLM_SCRIPT}'"
  sleep 1
  vllm_pid="$(cat "${RUN_DIR}/vllm.pid" 2>/dev/null || true)"
  if [[ -n "${vllm_pid}" ]] && ! kill -0 "${vllm_pid}" >/dev/null 2>&1; then
    log "vLLM process exited early; last logs:"
    tail -n 200 "${VLLM_LOG_FILE}" 2>/dev/null || true
    die "Failed to start vLLM. If you use a venv, set TXNEWS_VLLM_PYTHON to the correct python."
  fi
  if ! wait_http "http://127.0.0.1:${VLLM_PORT}/v1/models" "vLLM /v1/models" "${VLLM_TIMEOUT}"; then
    die "vLLM not ready (timeout). Check ${VLLM_LOG_FILE}."
  fi
  export TXNEWS_LLM_DEEP_BASE_URL="http://127.0.0.1:${VLLM_PORT}/v1"
  log "vLLM is ready. (TXNEWS_LLM_DEEP_BASE_URL=${TXNEWS_LLM_DEEP_BASE_URL})"
  VLLM_STARTED="1"
else
  log "Skip host vLLM (TXNEWS_ACCELERATOR=${ACCEL} TXNEWS_START_VLLM=${START_VLLM})."
fi

echo "== Start host processes (venv) =="

start_bg db_init python -m apps.db_init
start_bg bootstrap python -m apps.bootstrap

if [[ "${WEB_DEV}" == "1" ]]; then
  # Keep the only external port for Vite so frontend changes are visible on :8000.
  API_PORT="$(pick_free_port "${LOCAL_API_PORT}")"

  # Ensure Vite really binds to WEB_DEV_PORT (avoid auto fallback which may be blocked).
  # Always try cleaning up old tx-news listeners; some environments restrict `ss` output.
  kill_project_listeners_on_port "${WEB_DEV_PORT}"

  # Common cause: a previously started compose app stack still binds :8000.
  if compose_available; then
    compose stop api admin >/dev/null 2>&1 || true
  fi

  if port_is_listening "${WEB_DEV_PORT}"; then
    port_diag_ps "${WEB_DEV_PORT}"
    echo "ERROR: Port ${WEB_DEV_PORT} is still in use; cannot start Vite there." >&2
    echo "Hint: stop any old tx-news docker/app processes using :${WEB_DEV_PORT} and retry." >&2
    exit 2
  fi

  # Ensure Vite really binds to WEB_DEV_PORT (avoid auto fallback to 8003 which is often blocked).
  if ! have npm; then
    die "npm not found; install Node.js or rerun with --no-web-dev."
  fi
  if [[ "${WEB_DEV_MODE}" != "public" && "${WEB_DEV_MODE}" != "admin" ]]; then
    echo "Invalid --web-dev-mode: ${WEB_DEV_MODE} (expected public|admin)" >&2
    exit 2
  fi
  if [[ "${SKIP_WEB_INSTALL_DEPS}" != "1" && ! -d "apps/web/node_modules" ]]; then
    log "Installing web dependencies (apps/web)..."
    npm --prefix apps/web install
  fi

  # Let Vite proxy API calls to the correct backend port.
  start_bg_env web_dev \
    TXNEWS_API_PORT="${API_PORT}" \
    TXNEWS_ADMIN_PORT="${ADMIN_PORT}" \
    npm --prefix apps/web run dev -- --mode "${WEB_DEV_MODE}" --host 0.0.0.0 --port "${WEB_DEV_PORT}" --strictPort

  # Hard readiness check: ensure port 8000 actually serves Vite dev HTML.
  if ! python - <<PY
import time
from urllib.request import urlopen

url="http://127.0.0.1:${WEB_DEV_PORT}/"
deadline=time.time()+12
last_err=None
while time.time() < deadline:
    try:
        with urlopen(url, timeout=1.0) as r:
            body=(r.read(200000) or b"").decode("utf-8", "ignore")
        if "/@vite/client" in body:
            print("web_dev_ready= true url=", url)
            raise SystemExit(0)
        last_err=f"unexpected_html(no @vite/client)"
    except Exception as e:
        last_err=repr(e)
    time.sleep(0.3)
print("web_dev_ready= false url=", url, "err=", last_err)
raise SystemExit(2)
PY
  then
    tail -n 200 "${LOG_DIR}/web_dev.log" 2>/dev/null || true
    die "Vite dev server not ready on port ${WEB_DEV_PORT} (see ${LOG_DIR}/web_dev.log)."
  fi
fi

start_bg api uvicorn apps.api.main:app --host 0.0.0.0 --port "${API_PORT}" "${UVICORN_RELOAD_ARGS[@]}"
start_bg worker celery -A tx_news.tasks.celery_app.celery_app worker -l INFO --pool=solo --concurrency=1
start_bg beat celery -A tx_news.tasks.celery_app.celery_app beat -l INFO
start_bg nats_bridge python -m apps.worker.nats_bridge
start_bg collector python -m apps.collector.main
if [[ "${WITH_ADMIN}" == "1" ]]; then
  start_bg admin uvicorn apps.admin.main:app --host 0.0.0.0 --port "${ADMIN_PORT}" "${UVICORN_RELOAD_ARGS[@]}"
fi

log "Startup checks..."
if ! wait_http "http://127.0.0.1:${API_PORT}/health" "API /health" 120; then
  tail -n 200 "${LOG_DIR}/api.log" 2>/dev/null || true
  die "API not ready (timeout)."
fi
if have curl; then
  if curl -fsS --max-time 5 "http://127.0.0.1:${API_PORT}/status" 2>/dev/null | grep -Eq '"a_share_basic"[[:space:]]*:[[:space:]]*0'; then
    log "WARN: a_share_basic is empty (bootstrap may still be running or failed)."
    tail -n 200 "${LOG_DIR}/bootstrap.log" 2>/dev/null || true
  fi
fi
if [[ "${WITH_ADMIN}" == "1" ]]; then
  wait_http "http://127.0.0.1:${ADMIN_PORT}/health" "Admin /health" 120 || true
fi
if [[ "${VLLM_STARTED}" == "1" ]]; then
  wait_http "http://127.0.0.1:${VLLM_PORT}/v1/models" "vLLM /v1/models" 10 || true
fi
log "Startup complete."

echo
echo "PIDs: ${RUN_DIR}"
echo "Logs: ${LOG_DIR}"
echo "API:  http://localhost:${API_PORT}/status"
if [[ "${WITH_ADMIN}" == "1" ]]; then
  echo "Admin: http://localhost:${ADMIN_PORT}/"
fi
if [[ "${WEB_DEV}" == "1" ]]; then
  echo "Web UI (dev): http://localhost:${WEB_DEV_PORT}/ (mode=${WEB_DEV_MODE})"
  echo "NOTE: Your browser should use the Web UI URL (port ${WEB_DEV_PORT}) to see the latest frontend."
else
  echo "Web UI: http://localhost:${API_PORT}/ (UI build required in apps/web/dist_public)"
fi
if [[ "${VLLM_STARTED}" == "1" ]]; then
  echo "vLLM: http://localhost:${VLLM_PORT}/v1 (models: /v1/models)"
fi
echo "Stop: bash scripts/stop_local.sh"
