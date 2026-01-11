<!-- Input: 仓库结构与本地开发约定 -->
<!-- Output: 贡献者/协作者的可执行指南 -->
<!-- Pos: 仓库协作入口文档（变更时同步更新以上注释与所属目录 FOLDER.md） -->

# Repository Guidelines
*任何功能，架构，写法更新必须在工作结束后更新相关目录的子文档*
## Project Structure & Module Organization

- `src/tx_news/`: core library (crawler, normalize, dedup, embedding, storage, tasks, agent).
- `apps/`: runnable entrypoints/services:
  - `apps/collector/`: pulls sources and publishes to NATS.
  - `apps/worker/`: NATS→Celery bridge + pipeline workers.
  - `apps/api/`: FastAPI API + serves `apps/web/dist` (frontend).
  - `apps/web/`: Vue 3 + TypeScript frontend (SPA).
  - `apps/mcp/`: MCP server integration.
- `config/`: local configuration (`config.yaml`) and source list (`sources.txt`).
- `scripts/`: developer scripts (`start.sh`, `stop.sh`).
- `var/`: runtime logs/cache (gitignored).
- `finetune/`: optional fine-tuning + vLLM scripts.

## Build, Test, and Development Commands

- `bash scripts/start.sh`: starts the Docker stack (profile=app); in `TXNEWS_ACCELERATOR=gpu` mode it can also start host vLLM (outside Docker) when `TXNEWS_START_VLLM=1`.
- `bash scripts/stop.sh`: stops the Docker stack (profile=app; volumes are preserved) and tries to stop host vLLM started by `start.*`.
- `docker compose up -d`: starts infra only (Postgres/Redis/NATS/MinIO/Qdrant).
- `docker compose --profile app up -d --build`: starts infra + app services (API/Admin/Worker/Collector/NATS-Bridge).
- `uvicorn apps.api.main:app --reload --port 8000`: run API locally during development.
- `celery -A tx_news.tasks.celery_app.celery_app worker -l INFO --pool=solo --concurrency=1`: run a worker.

## Coding Style & Naming Conventions

- Python >= 3.10; use 4-space indentation and type hints.
- Linting: `ruff` (configured in `pyproject.toml`, line length 100). Run `ruff check .` (or `ruff check --fix .`).
- Prefer adding new reusable code under `src/tx_news/<area>/`; keep service wiring in `apps/<service>/`.
- When you change files/folders: update the file’s 3-line header and the nearest `FOLDER.md` index.

## Testing Guidelines

- Framework: `pytest` (dev deps in `requirements-dev.txt`).
- Run: `pip install -r requirements-dev.txt && pytest -q`.
- New tests: add `tests/test_*.py`; avoid network access and use fakes/mocks for external services.

## Commit & Pull Request Guidelines

- Commits follow a Conventional Commits-style prefix (e.g., `feat: ...`, `fix: ...`, `docs: ...`).
- PRs include: what changed, how to run/verify (commands above), and UI screenshots when touching `apps/web/` or `apps/api/static/`.

## Security & Configuration Tips

- Don’t commit secrets: `.env` is gitignored; copy from `.env.example`.
- Prefer environment variables for API keys (e.g., `DASHSCOPE_API_KEY`); treat `config/config.yaml` as local-only.
