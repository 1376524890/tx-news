<!-- Input: 部署/运行所需的系统、网络与密钥信息 -->
<!-- Output: 可执行的环境要求清单 -->
<!-- Pos: 根目录运行前置文档（变更时同步更新以上注释与所属目录 FOLDER.md） -->

# Requirements

## System
- Python >= 3.10
  - tx-news：开发/运行环境通常为 Python 3.10–3.12
- Docker + Docker Compose (for Postgres/Redis/NATS/MinIO/Qdrant)
- Node.js >= 18（用于构建 `apps/web`；若 `apps/web/dist` 已存在且不需要重新构建，可不安装）

## Network
- Runtime requires outbound network access for:
  - DashScope (`qwen3-max`) calls (if enabled)
  - Tushare A-share master data sync（可能受频率限制；默认 12h 缓存 TTL，过期才更新；失败自动回退到 AkShare 或本地缓存）
  - initial download of the local embedding model (unless you provide a local model path / cache); you can set `HF_ENDPOINT` (e.g. `https://hf-mirror.com`) to use a mirror

## GPU (optional)
- For GPU embeddings, install a CUDA-enabled PyTorch build (otherwise `embedding.device: cuda` will fail / fallback won’t happen).
- Multi-GPU is supported via process-level binding (e.g. set `CUDA_VISIBLE_DEVICES=0` for worker, `CUDA_VISIBLE_DEVICES=1` for API if needed).

## Secrets
- LLM API key: set `TXNEWS_LLM_API_KEY` (preferred; OpenAI-compatible) or `DASHSCOPE_API_KEY` (compat), or `llm.api_key` in `config/config.yaml`
- Tushare token: set `tushare.token` in `config/config.yaml`
