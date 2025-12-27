#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
LOG_DIR="${LOG_DIR:-var/log}"
RUN_DIR="${RUN_DIR:-.run}"

mkdir -p "${LOG_DIR}" "${RUN_DIR}"

die() {
  echo "ERROR: $*" >&2
  exit 1
}

have() {
  command -v "$1" >/dev/null 2>&1
}

compose() {
  if have docker && docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif have docker-compose; then
    docker-compose "$@"
  else
    die "docker compose not found (install Docker + Compose)"
  fi
}

wait_port() {
  local host="$1"
  local port="$2"
  local name="$3"
  local timeout="${4:-60}"
  echo "Waiting for ${name} on ${host}:${port} (timeout ${timeout}s)..."
  "${PYTHON_BIN}" - <<PY
import socket, time, sys
host="${host}"
port=int("${port}")
deadline=time.time()+int("${timeout}")
while time.time() < deadline:
  try:
    with socket.create_connection((host, port), timeout=2):
      print("OK")
      sys.exit(0)
  except OSError:
    time.sleep(1)
print("TIMEOUT", file=sys.stderr)
sys.exit(1)
PY
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
      echo "Already running: ${name} (pid=${pid})"
      return 0
    fi
    rm -f "${pidfile}"
  fi

  echo "Starting ${name}..."
  (bash -lc "${cmd}" >>"${logfile}" 2>&1) &
  echo $! > "${pidfile}"
  echo "Started ${name} pid=$(cat "${pidfile}") log=${logfile}"
}

stop_all() {
  echo "Stopping processes..."
  for pidfile in "${RUN_DIR}"/*.pid; do
    [[ -e "${pidfile}" ]] || continue
    local pid
    pid="$(cat "${pidfile}" || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
      echo "Killing $(basename "${pidfile}" .pid) pid=${pid}"
      kill "${pid}" >/dev/null 2>&1 || true
    fi
    rm -f "${pidfile}" || true
  done
  echo "Done."
}

trap stop_all INT TERM

have "${PYTHON_BIN}" || die "python not found: ${PYTHON_BIN}"

if [[ ! -d ".venv" ]]; then
  echo "Creating virtualenv in .venv ..."
  "${PYTHON_BIN}" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
pip install -r requirements.txt >/dev/null
pip install -e . >/dev/null

if [[ ! -f ".env" ]]; then
  echo "Creating .env from .env.example ..."
  cp .env.example .env
fi

# Export .env for processes started by this script.
set -a
# shellcheck disable=SC1091
source .env
set +a

echo "Starting Docker services..."
compose up -d

wait_port "127.0.0.1" "5432" "Postgres" 90
wait_port "127.0.0.1" "6379" "Redis" 60
wait_port "127.0.0.1" "4222" "NATS" 60
wait_port "127.0.0.1" "9000" "MinIO" 60
wait_port "127.0.0.1" "6333" "Qdrant" 60

# Optional: sync A-share master data if tushare.token is configured.
TUSHARE_TOKEN="$("${PYTHON_BIN}" - <<'PY'
import yaml
from pathlib import Path
p=Path("config/config.yaml")
cfg=yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
print(((cfg.get("tushare") or {}).get("token") or "").strip())
PY
)"
if [[ -n "${TUSHARE_TOKEN}" ]]; then
  echo "Syncing A-share master data from Tushare..."
  python -m apps.sync_tushare || echo "WARN: Tushare sync failed (check token/network)."
else
  echo "WARN: tushare.token is empty; skip A-share master data sync."
fi

start_bg "celery_worker" \
  "cd '${ROOT_DIR}' && source .venv/bin/activate && celery -A tx_news.tasks.celery_app.celery_app worker -l INFO" \
  "${LOG_DIR}/celery_worker.log"

start_bg "nats_bridge" \
  "cd '${ROOT_DIR}' && source .venv/bin/activate && python -m apps.worker.nats_bridge" \
  "${LOG_DIR}/nats_bridge.log"

start_bg "collector" \
  "cd '${ROOT_DIR}' && source .venv/bin/activate && python -m apps.collector.main" \
  "${LOG_DIR}/collector.log"

start_bg "api" \
  "cd '${ROOT_DIR}' && source .venv/bin/activate && uvicorn apps.api.main:app --host 0.0.0.0 --port 8000" \
  "${LOG_DIR}/api.log"

echo ""
echo "All services started."
echo "- API: http://localhost:8000 (health: /health, search: /search?q=...)"
echo "- Logs: ${LOG_DIR}/"
echo "Press Ctrl+C to stop background processes started by this script."
echo ""

while true; do
  sleep 2
done

