#!/usr/bin/env bash
# Input: .run/*.pid（由 scripts/start.sh 生成）
# Output: 停止本地后台进程（不自动停止 Docker services）
# Pos: 运维停止脚本（变更时同步更新以上注释与所属目录 FOLDER.md）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

RUN_DIR="${RUN_DIR:-.run}"

for pidfile in "${RUN_DIR}"/*.pid; do
  [[ -e "${pidfile}" ]] || continue
  pid="$(cat "${pidfile}" || true)"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
    echo "Killing $(basename "${pidfile}" .pid) pid=${pid}"
    kill "${pid}" >/dev/null 2>&1 || true
  fi
  rm -f "${pidfile}" || true
done

echo "Stopped. (Docker services are still running; use: docker compose down)"
