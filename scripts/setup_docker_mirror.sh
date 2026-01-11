#!/usr/bin/env bash
# Input: root/sudo permissions + a registry mirror URL (e.g. Aliyun/DaoCloud)
# Output: configure Docker daemon registry-mirrors and restart Docker
# Pos: Docker 国内拉取加速脚本（变更时同步更新以上注释与所属目录 FOLDER.md）
set -euo pipefail

MIRROR_URL="${1:-}"
if [[ -z "${MIRROR_URL}" ]]; then
  echo "Usage: bash scripts/setup_docker_mirror.sh <mirror-url>" >&2
  echo "Example: bash scripts/setup_docker_mirror.sh https://<your-mirror>" >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "This script needs root. Re-run with sudo." >&2
  exit 1
fi

cfg="/etc/docker/daemon.json"
tmp="$(mktemp)"
trap 'rm -f "${tmp}"' EXIT

PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "python3/python not found; cannot edit ${cfg} safely." >&2
  exit 1
fi

if [[ -f "${cfg}" ]]; then
  cp "${cfg}" "${cfg}.bak.$(date +%Y%m%d%H%M%S)"
fi

"${PYTHON_BIN}" - <<'PY' "${cfg}" "${tmp}" "${MIRROR_URL}"
import json, os, sys

cfg, out, mirror = sys.argv[1], sys.argv[2], sys.argv[3]
data = {}
if os.path.exists(cfg):
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        data = {}

mirrors = list(dict.fromkeys([mirror] + (data.get("registry-mirrors") or [])))
data["registry-mirrors"] = mirrors

with open(out, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY

install -m 0644 "${tmp}" "${cfg}"

if command -v systemctl >/dev/null 2>&1; then
  systemctl restart docker
else
  service docker restart || true
fi

echo "Updated ${cfg} with registry-mirrors=[${MIRROR_URL}] and restarted Docker."
