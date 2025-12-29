#!/usr/bin/env bash
# Input: 本地 Python/Docker/NVIDIA 环境 + config/.env + HuggingFace 镜像/缓存配置
# Output: 自动安装匹配的 torch、预检 embedding 下载/加载、启动 v0 单机栈并写入 var/log 与 .run
# Pos: 运维启动脚本（变更时同步更新以上注释与所属目录 FOLDER.md）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
LOG_DIR="${LOG_DIR:-var/log}"
RUN_DIR="${RUN_DIR:-.run}"
BOOTSTRAP_LOG="${BOOTSTRAP_LOG:-${LOG_DIR}/bootstrap.log}"

mkdir -p "${LOG_DIR}" "${RUN_DIR}"

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

setup_hf_env() {
  # HF_ENDPOINT controls HuggingFace Hub base URL for model downloads.
  # Default to hf-mirror for users in restricted networks.
  export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

  # Keep cache in-repo by default to avoid polluting ~ and ease cleanup.
  export HF_HOME="${HF_HOME:-${ROOT_DIR}/var/hf}"
  if [[ "${HF_HOME}" != /* ]]; then
    export HF_HOME="${ROOT_DIR}/${HF_HOME}"
  fi
  mkdir -p "${HF_HOME}"
}

detect_torch_variant() {
  # echo: cpu | cu121 | cu124
  if [[ -n "${TORCH_VARIANT:-}" ]]; then
    echo "${TORCH_VARIANT}"
    return 0
  fi

  if ! have nvidia-smi; then
    echo "cpu"
    return 0
  fi

  local cuda_ver major minor
  cuda_ver="$(nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: \\([0-9.]*\\).*/\\1/p' | head -n 1 || true)"
  if [[ -z "${cuda_ver}" ]]; then
    echo "cu121"
    return 0
  fi

  major="${cuda_ver%%.*}"
  minor="${cuda_ver#*.}"
  minor="${minor%%.*}"
  if [[ -z "${major}" || -z "${minor}" ]]; then
    echo "cu121"
    return 0
  fi

  if (( major > 12 )) || (( major == 12 && minor >= 4 )); then
    echo "cu124"
  else
    echo "cu121"
  fi
}

install_torch_auto() {
  local auto="${AUTO_TORCH:-1}"
  if [[ "${auto}" != "1" ]]; then
    log "AUTO_TORCH=0; skip torch auto-install."
    return 0
  fi

  local variant index_url
  variant="$(detect_torch_variant)"

  if python -c "import torch; print(torch.__version__)" >/dev/null 2>&1; then
    local torch_ver torch_cuda
    torch_ver="$(python -c 'import torch; print(torch.__version__)' 2>/dev/null || true)"
    torch_cuda="$(python -c 'import torch; print(getattr(torch.version, "cuda", "") or "")' 2>/dev/null || true)"
    if [[ "${variant}" != "cpu" && -z "${torch_cuda}" ]]; then
      log "Torch is installed but CUDA is not enabled; reinstalling PyTorch (${variant})..."
      pip uninstall -y torch 2>&1 | tee -a "${BOOTSTRAP_LOG}"
    else
      log "Torch already installed: ${torch_ver} (cuda=${torch_cuda:-none})"
      return 0
    fi
  fi

  if [[ -n "${TORCH_INDEX_URL:-}" ]]; then
    index_url="${TORCH_INDEX_URL}"
  else
    case "${variant}" in
      cpu) index_url="https://download.pytorch.org/whl/cpu" ;;
      cu121) index_url="https://download.pytorch.org/whl/cu121" ;;
      cu124) index_url="https://download.pytorch.org/whl/cu124" ;;
      *) die "invalid TORCH_VARIANT: ${variant} (expected cpu/cu121/cu124)" ;;
    esac
  fi

  log "Installing PyTorch (${variant}) from ${index_url} ..."
  pip install --upgrade "torch" --index-url "${index_url}" 2>&1 | tee -a "${BOOTSTRAP_LOG}"
}

preflight_embedding() {
  local enabled="${PREFLIGHT_EMBEDDING:-1}"
  if [[ "${enabled}" != "1" ]]; then
    log "PREFLIGHT_EMBEDDING=0; skip embedding preflight."
    return 0
  fi

  setup_hf_env

  log "Preflight: torch/GPU/HF/embedding..."
  python - <<'PY'
import os
import time

import yaml
from pathlib import Path

cfg_path = Path("config/config.yaml")
cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
embedding_cfg = (cfg.get("embedding") or {}) if isinstance(cfg, dict) else {}

device_cfg = str(embedding_cfg.get("device", "auto")).strip().lower()
model_name = str(embedding_cfg.get("model_name") or "").strip()

print("HF_ENDPOINT =", os.environ.get("HF_ENDPOINT"))
print("HF_HOME     =", os.environ.get("HF_HOME"))

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
            "Fix your torch CUDA build/driver, or set embedding.device=cpu."
        )

from tx_news.embedding.embedder import build_embedder

t0 = time.time()
embedder, strategy = build_embedder(embedding_cfg)
vec = embedder.embed("embedding 预检")
dt = time.time() - t0

print("embedding.model_name    =", model_name)
print("embedding.dim           =", len(vec))
print("qdrant.strategy         =", strategy)
print(f"embedding.preflight_sec = {dt:.2f}")
PY
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
  log "Waiting for ${name} on ${host}:${port} (timeout ${timeout}s)..."
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
      log "Already running: ${name} (pid=${pid})"
      return 0
    fi
    rm -f "${pidfile}"
  fi

  log "Starting ${name}..."
  (bash -lc "${cmd}" >>"${logfile}" 2>&1) &
  echo $! > "${pidfile}"
  log "Started ${name} pid=$(cat "${pidfile}") log=${logfile}"
}

stop_all() {
  log "Stopping processes..."
  for pidfile in "${RUN_DIR}"/*.pid; do
    [[ -e "${pidfile}" ]] || continue
    local pid
    pid="$(cat "${pidfile}" || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
      log "Killing $(basename "${pidfile}" .pid) pid=${pid}"
      kill "${pid}" >/dev/null 2>&1 || true
    fi
    rm -f "${pidfile}" || true
  done
  log "Done."
}

trap stop_all INT TERM

have "${PYTHON_BIN}" || die "python not found: ${PYTHON_BIN}"

if [[ ! -d ".venv" ]]; then
  log "Step 1/10: Creating virtualenv in .venv (this may take a moment)..."
  "${PYTHON_BIN}" -m venv .venv
  log "Virtualenv created."
else
  log "Step 1/10: Virtualenv already exists (.venv)."
fi

# shellcheck disable=SC1091
source .venv/bin/activate
log "Step 2/10: Upgrading pip..."
python -m pip install --upgrade pip 2>&1 | tee -a "${BOOTSTRAP_LOG}"

log "Step 3/10: Auto-installing torch (CPU/CUDA)..."
install_torch_auto

log "Step 4/10: Installing Python dependencies (requirements.txt)..."
pip install -r requirements.txt 2>&1 | tee -a "${BOOTSTRAP_LOG}"

log "Step 5/10: Installing this repo as editable package (pip install -e .)..."
pip install -e . 2>&1 | tee -a "${BOOTSTRAP_LOG}"
log "Python deps installed. (bootstrap log: ${BOOTSTRAP_LOG})"

log "Sanity check: verifying Celery tasks are registered..."
python - <<'PY'
from tx_news.tasks.celery_app import celery_app
keys = [k for k in celery_app.tasks.keys() if k.startswith("tx_news.tasks.")]
print(f"Registered tasks: {len(keys)}")
for k in sorted(keys):
    print(" -", k)
if not keys:
    raise SystemExit("ERROR: no tx_news.tasks.* registered; worker would discard tasks")
PY

if [[ ! -f ".env" ]]; then
  log "Creating .env from .env.example ..."
  cp .env.example .env
fi

# Export .env for processes started by this script.
set -a
# shellcheck disable=SC1091
source .env
set +a

log "Step 6/10: Preflight embedding (HF mirror + model load)..."
preflight_embedding

log "Step 7/10: Starting Docker services (postgres/redis/nats/minio/qdrant)..."
compose up -d

wait_port "127.0.0.1" "5432" "Postgres" 90
wait_port "127.0.0.1" "6379" "Redis" 60
wait_port "127.0.0.1" "4222" "NATS" 60
wait_port "127.0.0.1" "9000" "MinIO" 60
wait_port "127.0.0.1" "6333" "Qdrant" 60
log "Docker services are ready."

# Optional: sync A-share master data if tushare.token is configured.
log "Step 8/10: Optional Tushare A-share master data sync..."
TUSHARE_TOKEN="$("${PYTHON_BIN}" - <<'PY'
import yaml
from pathlib import Path
p=Path("config/config.yaml")
cfg=yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
print(((cfg.get("tushare") or {}).get("token") or "").strip())
PY
)"
if [[ -n "${TUSHARE_TOKEN}" ]]; then
  log "Syncing A-share master data from Tushare..."
  python -m apps.sync_tushare || echo "WARN: Tushare sync failed (check token/network)."
else
  log "WARN: tushare.token is empty; skip A-share master data sync."
fi

log "Step 9/10: Starting background processes (Celery worker / NATS bridge / Collector / API)..."
start_bg "celery_worker" \
  "cd '${ROOT_DIR}' && source .venv/bin/activate && celery -A tx_news.tasks.celery_app.celery_app worker -l INFO --pool=solo --concurrency=1" \
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

log "Step 10/10: Startup complete."
log "Web UI: http://localhost:8000/"
log "Admin UI: http://localhost:8000/admin"
log "API: http://localhost:8000 (health: /health, search: /search?q=...)"
log "Logs: ${LOG_DIR}/ (bootstrap: ${BOOTSTRAP_LOG})"
log "Stop: Ctrl+C here, or run: bash scripts/stop.sh"
log "Container services remain running until: docker compose down"

while true; do
  sleep 2
done
