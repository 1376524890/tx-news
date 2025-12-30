#!/usr/bin/env bash
# Input: LLaMA-Factory 环境 + finetune/sft.yaml（或自定义路径）
# Output: 触发 SFT 训练并写入 output_dir
# Pos: 训练脚本入口（变更时同步更新以上注释与所属目录 FOLDER.md）
set -euo pipefail

# This script assumes you have `llamafactory-cli` available in PATH.
# If your install exposes a different entrypoint, adjust accordingly.

CFG="${1:-finetune/sft.yaml}"
echo "Running SFT with config: ${CFG}"

if command -v llamafactory-cli >/dev/null 2>&1; then
  llamafactory-cli train "${CFG}"
  exit 0
fi

if python -c "import llamafactory" >/dev/null 2>&1; then
  python -m llamafactory.cli train "${CFG}"
  exit 0
fi

echo "[ERROR] LLaMA-Factory is not installed (missing 'llamafactory-cli' and python module 'llamafactory')." >&2
echo "Install it first, for example:" >&2
echo "  mkdir -p var/vendor" >&2
echo "  git clone https://github.com/hiyouga/LLaMA-Factory.git var/vendor/LLaMA-Factory" >&2
echo "  pip install -e var/vendor/LLaMA-Factory" >&2
exit 127

