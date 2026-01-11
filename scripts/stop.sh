#!/usr/bin/env bash
# Input: docker compose 编排 +（可选）.env/.run/vllm.pid
# Output: 停止本仓库 docker compose 全部服务，并尽力停止宿主机 vLLM（pidfile/端口探测）
# Pos: 运维停止脚本（Docker 版；变更时同步更新以上注释与所属目录 FOLDER.md）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

RUN_DIR="${RUN_DIR:-.run}"
LOG_DIR="${LOG_DIR:-var/log}"
mkdir -p "${RUN_DIR}" "${LOG_DIR}"

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
  echo "docker compose not available (install Docker + Compose; or run with sudo / add user to docker group)" >&2
  exit 1
}

kill_tree() {
  local pid="${1:-}"
  [[ -n "${pid}" ]] || return 0
  if kill -0 "${pid}" >/dev/null 2>&1; then
    # Prefer killing the whole process group (start.sh uses setsid when available).
    kill -- -"${pid}" >/dev/null 2>&1 || true
    kill "${pid}" >/dev/null 2>&1 || true
  fi
}

kill_vllm_by_port() {
  local port="${1:-9999}"
  local pids=""

  if have lsof; then
    pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null | tr '\n' ' ' || true)"
  elif have ss; then
    # Extract PID from: users:(("python",pid=1234,fd=...))
    pids="$(ss -ltnp 2>/dev/null | awk -v p=":${port}" '$4 ~ p && $0 ~ /pid=/ {print $0}' | sed -n 's/.*pid=\\([0-9][0-9]*\\).*/\\1/p' | sort -u | tr '\n' ' ' || true)"
  fi

  for pid in ${pids}; do
    [[ -n "${pid}" ]] || continue
    cmd="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
    if echo "${cmd}" | rg -q "vllm\\.entrypoints\\.openai\\.api_server|vllm\\s|api_server"; then
      echo "Killing vLLM on port ${port} pid=${pid} cmd=${cmd}"
      kill_tree "${pid}"
    else
      echo "Port ${port} is used by pid=${pid} (not vLLM); skip. cmd=${cmd}"
    fi
  done
}

TXNEWS_VLLM_PORT="${TXNEWS_VLLM_PORT:-9999}"
if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  TXNEWS_VLLM_PORT="${TXNEWS_VLLM_PORT:-9999}"
fi

if [[ -f "${RUN_DIR}/vllm.pid" ]]; then
  pid="$(cat "${RUN_DIR}/vllm.pid" || true)"
  echo "Killing host vLLM pid=${pid} (from ${RUN_DIR}/vllm.pid)"
  kill_tree "${pid}"
  rm -f "${RUN_DIR}/vllm.pid" || true
fi

kill_vllm_by_port "${TXNEWS_VLLM_PORT}"

if docker_accessible; then
  compose down
  echo "Stopped. (Data volumes are preserved; use: docker compose down -v)"
else
  echo "WARN: Docker daemon is not accessible from this shell; skipped stopping docker compose services." >&2
  echo "Run on a shell that can access Docker, e.g.:" >&2
  echo "  sudo docker compose down" >&2
  echo "  # or add your user to docker group and re-login" >&2
fi
