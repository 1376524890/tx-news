# Finetune starter (LLaMA-Factory)

This repo does **not** run finetuning automatically, but provides a ready-to-fill template so you can start SFT quickly.

## Prerequisites
- Install LLaMA-Factory in your environment (recommended: clone the repo and follow its install docs).
- Prepare:
  - a base model directory (local path)
  - SFT dataset file(s) (local path; JSON/JSONL as required by your LLaMA-Factory version)

## Quick start
1) Copy and edit `finetune/sft.yaml`:
   - set `model_name_or_path`
   - set `dataset_dir` and `dataset` (or direct dataset path, depending on your LLaMA-Factory version)
   - set `output_dir`
2) Run:
   - `bash finetune/run_sft.sh`

## Serving
`finetune/serve_vllm.sh` is a helper to start `vLLM` OpenAI-compatible server after you have a trained model/adapter.

