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

## Install LLaMA-Factory (repo clone)
This project keeps finetune tooling optional. A simple way to install LLaMA-Factory into your current `.venv`:
```bash
mkdir -p var/vendor
git clone https://github.com/hiyouga/LLaMA-Factory.git var/vendor/LLaMA-Factory
pip install -e var/vendor/LLaMA-Factory
```

## Dataset (Deep Analysis)
This repo includes a starter dataset and dataset registry for LLaMA-Factory:
- Dataset directory: `finetune/txdatasets/`
- Dataset name: `txnews_deep_analysis_sft`
- Dataset file: `finetune/txdatasets/txnews_deep_analysis_sft_alpaca.jsonl`

## Quick start
1) Copy and edit `finetune/sft.yaml`:
   - set `model_name_or_path`
   - set `dataset_dir` to `finetune/txdatasets` and `dataset` to `txnews_deep_analysis_sft`
   - set `output_dir`
2) Run:
   - `bash finetune/run_sft.sh`

## Serving
This repo assumes you run vLLM from a dedicated conda env `vllm`.

- Generic helper: `finetune/serve_vllm.sh` (fill `MODEL_DIR`, choose a port).
- For the bundled merged model (GPU0 + port 9999): `finetune/result_model/deepseekr1_merged/serve_vllm_gpu0_9999.sh`

After vLLM is up, point **deep analysis only** to it via `config/config.yaml`:
- `llm.deep.base_url: http://127.0.0.1:9999/v1`
- `llm.deep.model: deepseekr1-merged`
- keep chat on cloud via `llm.chat.*`
You can also control CPU/GPU behavior via `.env`:
- `TXNEWS_ACCELERATOR=gpu`: deep analysis uses local vLLM; embedding defaults to GPU (recommended set `TXNEWS_EMBEDDING_DEVICE=cuda:1`)
- `TXNEWS_ACCELERATOR=cpu`: deep analysis falls back to online LLM (`llm.chat`) as a safe baseline

## How to use it for Deep Analysis only (recommended)
To keep chat and normal analysis on API LLM while running **deep analysis** on local model, the recommended wiring is:
- Route `tx_news.tasks.deep_analysis.*` to a dedicated Celery queue (e.g. `deep`).
- Start a dedicated worker consuming only `deep` with env pointing to your local vLLM.
- Keep the default worker (pipeline + analyze) and API process pointing to your API LLM.

This is described at a high level in `README.md` (section “11.4”).
