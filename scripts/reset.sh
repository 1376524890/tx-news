#!/usr/bin/env bash
# Input: docker compose 基础设施服务 +（可选）本地缓存目录
# Output: 清空基础设施数据卷，实现 0 数据冷启动
# Pos: 运维重置脚本（变更时同步更新以上注释与所属目录 FOLDER.md）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

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

docker_cmd() {
  if have docker && docker info >/dev/null 2>&1; then
    docker "$@"
    return
  fi
  if have sudo && sudo -n docker info >/dev/null 2>&1; then
    sudo docker "$@"
    return
  fi
  echo "docker is not accessible from this shell." >&2
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
  return 2
}

usage() {
  cat <<'EOF'
Usage:
  bash scripts/reset.sh [--yes] [--keep-hf] [--purge-local]

Default:
  - Stops compose services (infra + app if running)
  - Deletes docker volumes: postgres_data, redis_data, minio_data, qdrant_data, hf_cache
  - Leaves local caches untouched

Flags:
  --yes          skip confirmation prompt
  --keep-hf      keep hf_cache volume (model cache)
  --purge-local  also remove local .run and var/cache
EOF
}

ASSUME_YES="0"
KEEP_HF="0"
PURGE_LOCAL="0"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) ASSUME_YES="1"; shift ;;
    --keep-hf) KEEP_HF="1"; shift ;;
    --purge-local) PURGE_LOCAL="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ "${ASSUME_YES}" != "1" ]]; then
  cat <<'EOF'
WARNING:
  This will DELETE docker volumes for Postgres/Redis/MinIO/Qdrant (and hf_cache unless --keep-hf).
  All data will be lost.

Type 'yes' to continue:
EOF
  read -r confirm
  if [[ "${confirm}" != "yes" ]]; then
    echo "Abort."
    exit 1
  fi
fi

if docker_accessible; then
  echo "== Stop docker compose services =="
  compose down --remove-orphans || true
else
  echo "WARN: Docker daemon is not accessible; skip compose down." >&2
fi

PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(basename "${ROOT_DIR}")}"
VOLUMES=(postgres_data redis_data minio_data qdrant_data)
if [[ "${KEEP_HF}" != "1" ]]; then
  VOLUMES+=(hf_cache)
fi

if docker_accessible; then
  echo "== Remove docker volumes =="
  for v in "${VOLUMES[@]}"; do
    name="${PROJECT_NAME}_${v}"
    echo "remove volume: ${name}"
    docker_cmd volume rm -f "${name}" >/dev/null 2>&1 || true
  done
else
  echo "WARN: Docker daemon is not accessible; skip volume removal." >&2
fi

if [[ "${PURGE_LOCAL}" == "1" ]]; then
  echo "== Purge local cache =="
  rm -rf .run var/cache || true
fi

echo "Reset complete."
