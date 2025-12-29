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

python -m vllm.entrypoints.openai.api_server \
  --model "${MODEL_DIR}" \
  --host "${HOST}" \
  --port "${PORT}"
