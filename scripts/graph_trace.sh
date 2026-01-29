#!/usr/bin/env bash
# Input: worker 日志（默认 var/log/local/worker.log）
# Output: 过滤后的 graphflow 关键路径日志（analysis -> KG -> causal）
# Pos: 运维排障（图谱流程追踪工具；变更时同步更新以上注释与所属目录 FOLDER.md）
set -euo pipefail

have() { command -v "$1" >/dev/null 2>&1; }

follow="0"
log_file="var/log/local/worker.log"

if [[ "${1:-}" == "--follow" ]]; then
  follow="1"
  shift
fi
if [[ -n "${1:-}" ]]; then
  log_file="$1"
fi

if [[ ! -f "${log_file}" ]]; then
  echo "log file not found: ${log_file}" >&2
  exit 2
fi

filter_cmd() {
  if have rg; then
    rg --line-buffered "graphflow" "$@"
  else
    grep --line-buffered -E "graphflow" "$@"
  fi
}

if [[ "${follow}" == "1" ]]; then
  tail -n 200 -f "${log_file}" | filter_cmd
else
  filter_cmd "${log_file}"
fi
