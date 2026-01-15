#!/usr/bin/env bash
# Input: .run/local/*.pid +（可选）docker compose（仅 infra）
# Output: 停止 start_local.sh 启动的宿主机进程；可选停止 infra 容器
# Pos: 运维入口（本地非 Docker 主程序一键停止）（变更时同步更新以上注释与所属目录 FOLDER.md）
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
  bash scripts/stop_local.sh [--keep-infra]

Default:
  - Stops host processes started by start_local.sh
  - If start_local.sh started infra (docker compose up), also runs `docker compose down`

Flags:
  --keep-infra  do not stop infra containers
EOF
}

KEEP_INFRA="0"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-infra) KEEP_INFRA="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

RUN_DIR="${TXNEWS_LOCAL_RUN_DIR:-.run/local}"
INFRA_MARK="${RUN_DIR}/infra.started"

if [[ ! -d "${RUN_DIR}" ]]; then
  echo "No run dir: ${RUN_DIR} (nothing to stop)."
  exit 0
fi

stop_pidfile() {
  local pid_file="$1"
  local name
  name="$(basename "${pid_file}" .pid)"
  local pid
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ -z "${pid}" ]]; then
    rm -f "${pid_file}"
    return 0
  fi

  if ! kill -0 "${pid}" >/dev/null 2>&1; then
    rm -f "${pid_file}"
    echo "gone: ${name} (pid=${pid})"
    return 0
  fi

  echo "stop: ${name} (pid=${pid})"
  kill "${pid}" >/dev/null 2>&1 || true
  for _ in $(seq 1 30); do
    if ! kill -0 "${pid}" >/dev/null 2>&1; then
      rm -f "${pid_file}"
      echo "stopped: ${name}"
      return 0
    fi
    sleep 0.2
  done
  echo "kill -9: ${name} (pid=${pid})"
  kill -9 "${pid}" >/dev/null 2>&1 || true
  rm -f "${pid_file}"
}

echo "== Stop host processes =="
shopt -s nullglob
for f in "${RUN_DIR}"/*.pid; do
  stop_pidfile "${f}"
done

echo "== Cleanup stray tx-news dev processes (best-effort) =="
pids=()
while IFS= read -r line; do
  # shellcheck disable=SC2206
  parts=($line)
  pid="${parts[0]}"
  cmd="${line#${pid} }"

  # Prefer very specific patterns to avoid killing unrelated processes.
  if [[ "${cmd}" == *"apps.api.main:app"* || "${cmd}" == *"apps.admin.main:app"* ]]; then
    pids+=("${pid}")
    continue
  fi
  if [[ "${cmd}" == *"${ROOT_DIR}/apps/web/node_modules/.bin/vite"* ]]; then
    pids+=("${pid}")
    continue
  fi
  if [[ "${cmd}" == *"${ROOT_DIR}"* && ( "${cmd}" == *"celery -A tx_news"* || "${cmd}" == *"python -m apps.worker.nats_bridge"* || "${cmd}" == *"python -m apps.collector.main"* ) ]]; then
    pids+=("${pid}")
    continue
  fi
done < <(ps -eo pid=,args=)

if [[ "${#pids[@]}" -gt 0 ]]; then
  echo "kill_pids=${pids[*]}"
  for pid in "${pids[@]}"; do
    kill "${pid}" >/dev/null 2>&1 || true
  done
fi

if [[ "${KEEP_INFRA}" != "1" && -f "${INFRA_MARK}" ]]; then
  echo "== Stop infra (docker compose down) =="
  compose down || true
  rm -f "${INFRA_MARK}" || true
fi
