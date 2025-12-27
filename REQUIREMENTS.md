# Requirements

## System
- Python >= 3.10
- Docker + Docker Compose (for Postgres/Redis/NATS/MinIO/Qdrant)

## Network
- Runtime requires outbound network access for:
  - DashScope (`qwen3-max`) calls (if enabled)
  - Tushare A-share master data sync
  - initial download of the local embedding model (unless you provide a local model path)

## Secrets
- DashScope API key: set `DASHSCOPE_API_KEY` or `llm.api_key` in `config/config.yaml`
- Tushare token: set `tushare.token` in `config/config.yaml`

