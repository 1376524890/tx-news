<!-- Input: 部署/运行所需的系统、网络与密钥信息 -->
<!-- Output: 可执行的环境要求清单 -->
<!-- Pos: 根目录运行前置文档（变更时同步更新以上注释与所属目录 FOLDER.md） -->

# Requirements

## System
- Docker + Docker Compose（必需；用于一键构建/启动全栈）
- （开发/二次开发）Python >= 3.10（通常为 3.10–3.12）
- （开发/二次开发）Node.js >= 18（前端本地开发；Docker 构建不要求宿主机安装 Node.js）

## Network
- Runtime requires outbound network access for:
  - DashScope (`qwen3-max`) calls (if enabled)
  - Tushare A-share master data sync（可能受频率限制；默认 12h 缓存 TTL，过期才更新；失败自动回退到 AkShare 或本地缓存）
  - initial download of the local embedding model (unless you provide a local model path / cache); you can set `HF_ENDPOINT` (e.g. `https://hf-mirror.com`) to use a mirror

## CN Mirrors (optional)
If you are in Mainland China and Docker build/pulls are slow, you can enable mirrors:

- **pip/npm/apt (build-time)**: set in `.env` (see `.env.example`):
  - `TXNEWS_PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/`
  - `TXNEWS_PIP_TRUSTED_HOST=mirrors.aliyun.com`
  - `TXNEWS_NPM_REGISTRY=https://registry.npmmirror.com`
  - `TXNEWS_DEBIAN_MIRROR=http://mirrors.aliyun.com/debian`
  - `TXNEWS_DEBIAN_SECURITY_MIRROR=http://mirrors.aliyun.com/debian-security`

- **Docker image pulls (daemon-level)**: configure your Docker daemon registry mirror (example):
  - Linux: edit `/etc/docker/daemon.json` and add `{"registry-mirrors":["https://<your-mirror>"]}` then restart Docker.
  - Docker Desktop: Settings → Docker Engine → add `registry-mirrors`.

## GPU (optional)
- vLLM 按“宿主机单独运行（Docker 外）”接入：需要 NVIDIA GPU + 驱动，并能在宿主机启动 OpenAI-compatible `/v1` 服务（例如用 `finetune/result_model/deepseekr1_merged/serve_vllm_gpu0_9999.sh`；要求本机具备 `conda`/`vllm` 环境）。
- Docker 容器通过 `TXNEWS_LLM_DEEP_BASE_URL` 连接宿主机 vLLM（compose 默认 `http://host.docker.internal:9999/v1`，并添加 `host-gateway` 映射）。
- 本项目 embedding 默认使用 CPU（除非显式设置 `TXNEWS_EMBEDDING_DEVICE`）。

## Secrets
- LLM API key: set `TXNEWS_LLM_API_KEY` (preferred; OpenAI-compatible) or `DASHSCOPE_API_KEY` (compat), or `llm.api_key` in `config/config.yaml`
- Tushare token: set `tushare.token` in `config/config.yaml`
