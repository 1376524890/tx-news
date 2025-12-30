<!-- Input: 微调/推理服务的外部工具与本地模型/数据路径 -->
<!-- Output: finetune/ 目录的使用说明与执行入口 -->
<!-- Pos: finetune 文档（变更时同步更新以上注释与所属目录 FOLDER.md） -->

# Finetune starter (LLaMA-Factory)

This repo does **not** run finetuning automatically, but provides a ready-to-fill template so you can start SFT quickly.

## Goal in this iteration
- Use a **local fine-tuned model** for the **deep analysis** task (worker-side `deep_optimize`) to reduce token cost and improve domain format stability.
- Keep **chat** (`/chat` and `/chat/stream`) on **API LLM** to preserve answer quality.

## Prerequisites
- Install LLaMA-Factory in your environment (recommended: clone the repo and follow its install docs).
- Prepare:
  - a base model directory (local path)
  - SFT dataset file(s) (local path; JSON/JSONL as required by your LLaMA-Factory version)

## Dataset (Deep Analysis)
This repo includes a starter dataset and dataset registry for LLaMA-Factory:
- Dataset directory: `finetune/datasets/`
- Dataset name: `txnews_deep_analysis_sft`
- Dataset file: `finetune/datasets/txnews_deep_analysis_sft_alpaca.jsonl`

## Quick start
1) Copy and edit `finetune/sft.yaml`:
   - set `model_name_or_path`
   - set `dataset_dir` to `finetune/datasets` and `dataset` to `txnews_deep_analysis_sft`
   - set `output_dir`
2) Run:
   - `bash finetune/run_sft.sh`

## Serving
`finetune/serve_vllm.sh` is a helper to start `vLLM` OpenAI-compatible server after you have a trained model/adapter.

## How to use it for Deep Analysis only (recommended)
To keep chat and normal analysis on API LLM while running **deep analysis** on local model, the recommended wiring is:
- Route `tx_news.tasks.deep_analysis.*` to a dedicated Celery queue (e.g. `deep`).
- Start a dedicated worker consuming only `deep` with env pointing to your local vLLM.
- Keep the default worker (pipeline + analyze) and API process pointing to your API LLM.

This is described at a high level in `README.md` (section “11.4”).
