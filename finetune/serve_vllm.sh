#!/usr/bin/env bash
# Input: 本地模型目录（MODEL_DIR）与 vLLM 环境
# Output: 启动 OpenAI 兼容的 vLLM API 服务
# Pos: 推理服务脚本入口（变更时同步更新以上注释与所属目录 FOLDER.md）
set -euo pipefail

# Example helper for vLLM OpenAI-compatible serving.
# Fill MODEL_DIR (base model or merged model) and optionally LORA_DIR.

MODEL_DIR="${MODEL_DIR:-/path/to/model}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8001}"

VLLM_CONDA_ENV="${TXNEWS_VLLM_CONDA_ENV:-${VLLM_CONDA_ENV:-vllm}}"
VLLM_CONDA_PYTHON="${TXNEWS_VLLM_CONDA_PYTHON:-${VLLM_CONDA_PYTHON:-3.11}}"
VLLM_AUTO_INSTALL="${TXNEWS_VLLM_AUTO_INSTALL:-${VLLM_AUTO_INSTALL:-1}}"
VLLM_RECREATE_CONDA_ENV="${TXNEWS_VLLM_RECREATE_CONDA_ENV:-${VLLM_RECREATE_CONDA_ENV:-0}}"
VLLM_PIP_SPEC="${TXNEWS_VLLM_PIP_SPEC:-${VLLM_PIP_SPEC:-vllm}}"

if [[ -z "${TXNEWS_VLLM_PYTHON:-}" && -z "${VLLM_PYTHON:-}" ]] && command -v conda >/dev/null 2>&1; then
  have_env() {
    conda env list 2>/dev/null | awk '{print $1}' | grep -qx "${VLLM_CONDA_ENV}"
  }
  ensure_env() {
    if [[ "${VLLM_RECREATE_CONDA_ENV}" == "1" ]] && have_env; then
      conda env remove -y -n "${VLLM_CONDA_ENV}" >&2
    fi
    if ! have_env; then
      conda create -y -n "${VLLM_CONDA_ENV}" "python=${VLLM_CONDA_PYTHON}" pip >&2
    fi
  }
  ensure_vllm() {
    if conda run -n "${VLLM_CONDA_ENV}" python -c "import vllm" >/dev/null 2>&1; then
      return 0
    fi
    if [[ "${VLLM_AUTO_INSTALL}" != "1" ]]; then
      return 1
    fi
    conda run -n "${VLLM_CONDA_ENV}" python -m pip install --no-cache-dir -U pip >&2
    conda run -n "${VLLM_CONDA_ENV}" python -m pip install --no-cache-dir -U "${VLLM_PIP_SPEC}" >&2
    conda run -n "${VLLM_CONDA_ENV}" python -c "import vllm" >/dev/null 2>&1
  }

  ensure_env
  if ! ensure_vllm; then
    echo "Failed to prepare vLLM in conda env '${VLLM_CONDA_ENV}'. Set TXNEWS_VLLM_PYTHON to use a venv python instead." >&2
    exit 1
  fi

  exec conda run -n "${VLLM_CONDA_ENV}" --no-capture-output \
    python -m vllm.entrypoints.openai.api_server \
    --model "${MODEL_DIR}" \
    --host "${HOST}" \
    --port "${PORT}"
fi

PY="${TXNEWS_VLLM_PYTHON:-${VLLM_PYTHON:-python}}"
if ! command -v "${PY}" >/dev/null 2>&1; then
  echo "python not found: ${PY}" >&2
  exit 1
fi
if ! "${PY}" -c "import vllm" >/dev/null 2>&1; then
  echo "vLLM not found for python=${PY}" >&2
  echo "Install vLLM into that environment, or set TXNEWS_VLLM_PYTHON to a python that has vllm." >&2
  echo "Example: ${PY} -m pip install -U vllm" >&2
  exit 1
fi

exec "${PY}" -m vllm.entrypoints.openai.api_server \
  --model "${MODEL_DIR}" \
  --host "${HOST}" \
  --port "${PORT}"
