# Requirements

## System
- Python >= 3.10
  - tx-news：已在 Python 3.13 测试
- Docker + Docker Compose (for Postgres/Redis/NATS/MinIO/Qdrant)
  - Web UI 随 API 一起启动（不需要 Node）

## Network
- Runtime requires outbound network access for:
  - DashScope (`qwen3-max`) calls (if enabled)
  - Tushare A-share master data sync（可能受频率限制；系统会自动回退到 AkShare 或本地缓存）
  - initial download of the local embedding model (unless you provide a local model path)

## Secrets
- DashScope API key: set `DASHSCOPE_API_KEY` or `llm.api_key` in `config/config.yaml`
- Tushare token: set `tushare.token` in `config/config.yaml`
