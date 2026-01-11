<!-- Input: 仓库根目录结构与关键文件 -->
<!-- Output: 根目录架构概览与文件清单 -->
<!-- Pos: 根目录索引文档（变更时同步更新以上注释与本文件内容） -->

# 根目录（Repository Root）

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 单机数据流（v1）：`apps/collector` → NATS → `apps/worker`(Celery) → Postgres/Qdrant/MinIO。
- 运行入口在 `apps/`，可复用库在 `src/tx_news/`，配置在 `config/`。
- 运维脚本在 `scripts/`；主程序推荐通过 Docker 运行（脚本会同步输出容器日志并落盘到 `var/log/compose.log`）；运行态目录（`var/`、`.run/`）均 gitignore。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `README.md` | 主文档 | 快速开始、API/UI 使用、架构与技术细节、取舍与路线图。 |
| `AGENTS.md` | 贡献指南 | 本仓库的开发与协作约定。 |
| `REQUIREMENTS.md` | 运维文档 | 环境/依赖/网络与密钥要求。 |
| `docker-compose.yml` | 单机编排 | 默认启动基础设施；`profile=app` 时构建并启动主程序容器（含 `db-init` 建表与 `bootstrap` 一次性引导主数据）。 |
| `.dockerignore` | Docker 构建 | 缩小构建上下文，避免把运行态/缓存打进镜像。 |
| `pyproject.toml` | 工具/打包配置 | `setuptools` + `ruff` 等配置入口。 |
| `requirements.txt` | 运行依赖 | 服务运行所需 Python 依赖。 |
| `requirements-dev.txt` | 开发依赖 | `pytest`/`ruff` 等开发工具依赖。 |
| `.env.example` | 环境变量样例 | `.env` 模板（密钥/连接串建议通过 env 注入）。 |
| `.gitignore` | 仓库维护 | 忽略本地运行态与缓存文件。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |

## 子目录

| 子目录 | 地位 | 功能 |
| --- | --- | --- |
| `apps/` | 运行入口层 | Collector/API/Worker/MCP 等可执行模块。 |
| `src/tx_news/` | 核心库 | 采集、清洗、去重、存储、任务、Agent。 |
| `config/` | 配置层 | `config.yaml` 与抓取源列表。 |
| `scripts/` | 运维脚本 | 一键启动/停止与本地开发辅助。 |
| `docker/` | 镜像定义 | 多目标 Dockerfile（按功能拆分构建镜像）。 |
| `deploy/` | 部署清单 | Kubernetes 等部署侧资产（预留）。 |
| `finetune/` | 可选模块 | SFT/vLLM 相关脚本与配置。 |
