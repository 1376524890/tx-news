#!/usr/bin/env bash
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

