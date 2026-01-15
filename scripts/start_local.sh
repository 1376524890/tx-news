#!/usr/bin/env bash
# Input: 本地 Python(v>=3.10) + venv + Docker(仅 infra) + .env/config/*
# Output: infra 使用 docker compose；主程序使用 venv 在宿主机启动（api/worker/collector/nats-bridge/admin）
# Pos: 运维入口（本地非 Docker 主程序一键启动）（变更时同步更新以上注释与所属目录 FOLDER.md）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

have() { command -v "$1" >/dev/null 2>&1; }

compose() {
  if have docker && docker info >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    docker compose "$@"
    return
  fi
  if have docker-compose; then
    docker-compose "$@"
    return
  fi
  echo "docker compose not available in this shell." >&2
  return 2
}

usage() {
  cat <<'EOF'
Usage:
  bash scripts/start_local.sh [--with-admin] [--reload] [--skip-infra] [--skip-install]
                           [--web-dev] [--no-web-dev] [--web-dev-mode public|admin]

What it does:
  - Starts infra only via docker compose (postgres/redis/nats/minio/qdrant)
  - Starts app processes on the host using a venv:
      - API (uvicorn, :8000)
      - Celery worker
      - NATS bridge
      - Collector
      - (optional) Admin/config UI (:8001)
  - If venv is missing, it will be created automatically; dependencies are installed via pip by default.
  - By default starts Vite dev server for the web UI (hot reload) on the external port (default 8000).
    In this mode the API is moved to 8001 so the browser only needs port 8000.

Env knobs:
  - TXNEWS_VENV_DIR (default: .venv)
  - TXNEWS_VENV_PYTHON (default: python3, fallback: python)
  - TXNEWS_LOCAL_LOG_DIR (default: var/log/local)
  - TXNEWS_LOCAL_RUN_DIR (default: .run/local)
  - TXNEWS_WEB_DEV_PORT (default: 8000)
  - TXNEWS_LOCAL_API_PORT (default: 18000; only used when WEB dev is enabled)
EOF
}

WITH_ADMIN="0"
RELOAD="0"
SKIP_INFRA="0"
SKIP_INSTALL_DEPS="0"
WEB_DEV="1"
WEB_DEV_MODE="public"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-admin) WITH_ADMIN="1"; shift ;;
    --reload) RELOAD="1"; shift ;;
    --skip-infra) SKIP_INFRA="1"; shift ;;
    --skip-install) SKIP_INSTALL_DEPS="1"; shift ;;
    --web-dev) WEB_DEV="1"; shift ;;
    --no-web-dev) WEB_DEV="0"; shift ;;
    --web-dev-mode) WEB_DEV_MODE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

VENV_DIR="${TXNEWS_VENV_DIR:-.venv}"
VENV_PYTHON="${TXNEWS_VENV_PYTHON:-python3}"
LOG_DIR="${TXNEWS_LOCAL_LOG_DIR:-var/log/local}"
RUN_DIR="${TXNEWS_LOCAL_RUN_DIR:-.run/local}"
WEB_DEV_PORT="${TXNEWS_WEB_DEV_PORT:-8000}"
API_PORT="${TXNEWS_API_PORT:-8000}"
ADMIN_PORT="${TXNEWS_ADMIN_PORT:-8001}"
LOCAL_API_PORT="${TXNEWS_LOCAL_API_PORT:-18000}"

mkdir -p "${LOG_DIR}" "${RUN_DIR}"

port_is_listening() {
  local port="$1"
  if have ss; then
    ss -ltn "( sport = :${port} )" 2>/dev/null | tail -n +2 | rg -q ':'
    return $?
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

echo "== Cleanup previous local processes (best-effort) =="
shopt -s nullglob
for f in "${RUN_DIR}"/*.pid; do
  stop_pidfile "${f}"
done

if [[ "${SKIP_INFRA}" != "1" ]]; then
  echo "== Start infra (docker compose) =="
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

port_is_listening() {
  local port="$1"
  if have ss; then
    ss -ltn "( sport = :${port} )" 2>/dev/null | tail -n +2 | rg -q ':'
    return $?
  fi
  # No reliable tool available; assume not listening.
  return 1
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
  ps -eo pid=,args= | rg --fixed-strings " --port ${port}" || true
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

echo "== Start host processes (venv) =="

if [[ "${WEB_DEV}" == "1" ]]; then
  # Keep the only external port for Vite so frontend changes are visible on :8000.
  API_PORT="$(pick_free_port "${LOCAL_API_PORT}")"

  # Ensure Vite really binds to WEB_DEV_PORT (avoid auto fallback which may be blocked).
  # Always try cleaning up old tx-news listeners; some environments restrict `ss` output.
  kill_project_listeners_on_port "${WEB_DEV_PORT}"

  # Common cause: a previously started compose app stack still binds :8000.
  if have docker && docker info >/dev/null 2>&1; then
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
    echo "WARN: npm not found; skipping --web-dev." >&2
  else
    if [[ "${WEB_DEV_MODE}" != "public" && "${WEB_DEV_MODE}" != "admin" ]]; then
      echo "Invalid --web-dev-mode: ${WEB_DEV_MODE} (expected public|admin)" >&2
      exit 2
    fi
    # Let Vite proxy API calls to the correct backend port.
    start_bg_env web_dev \
      TXNEWS_API_PORT="${API_PORT}" \
      TXNEWS_ADMIN_PORT="${ADMIN_PORT}" \
      npm --prefix apps/web run dev -- --mode "${WEB_DEV_MODE}" --host 0.0.0.0 --port "${WEB_DEV_PORT}" --strictPort

    # Hard readiness check: ensure port 8000 actually serves Vite dev HTML.
    python - <<PY
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
  fi
fi

start_bg api uvicorn apps.api.main:app --host 0.0.0.0 --port "${API_PORT}" "${UVICORN_RELOAD_ARGS[@]}"
start_bg worker celery -A tx_news.tasks.celery_app.celery_app worker -l INFO --pool=solo --concurrency=1
start_bg nats_bridge python -m apps.worker.nats_bridge
start_bg collector python -m apps.collector.main
if [[ "${WITH_ADMIN}" == "1" ]]; then
  start_bg admin uvicorn apps.admin.main:app --host 0.0.0.0 --port "${ADMIN_PORT}" "${UVICORN_RELOAD_ARGS[@]}"
fi

echo
echo "PIDs: ${RUN_DIR}"
echo "Logs: ${LOG_DIR}"
echo "API:  http://localhost:${API_PORT}/status"
if [[ "${WITH_ADMIN}" == "1" ]]; then
  echo "Admin: http://localhost:${ADMIN_PORT}/ (UI build may be missing)"
fi
if [[ "${WEB_DEV}" == "1" ]]; then
  echo "Web(dev): http://localhost:${WEB_DEV_PORT}/ (mode=${WEB_DEV_MODE})"
  echo "NOTE: Your browser should use the Web(dev) URL (port ${WEB_DEV_PORT}) to see live frontend changes."
fi
echo "Stop: bash scripts/stop_local.sh"
