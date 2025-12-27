#!/usr/bin/env bash
set -euo pipefail

# This script assumes you have `llamafactory-cli` available in PATH.
# If your install exposes a different entrypoint, adjust accordingly.

CFG="${1:-finetune/sft.yaml}"
echo "Running SFT with config: ${CFG}"

llamafactory-cli train "${CFG}"

