#!/usr/bin/env bash
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
