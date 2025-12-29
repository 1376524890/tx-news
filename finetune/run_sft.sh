#!/usr/bin/env bash
# Input: LLaMA-Factory 环境 + finetune/sft.yaml（或自定义路径）
# Output: 触发 SFT 训练并写入 output_dir
# Pos: 训练脚本入口（变更时同步更新以上注释与所属目录 FOLDER.md）
set -euo pipefail

# This script assumes you have `llamafactory-cli` available in PATH.
# If your install exposes a different entrypoint, adjust accordingly.

CFG="${1:-finetune/sft.yaml}"
echo "Running SFT with config: ${CFG}"

llamafactory-cli train "${CFG}"
