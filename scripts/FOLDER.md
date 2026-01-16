<!-- Input: 本地环境（Python/Docker）与仓库配置文件 -->
<!-- Output: 启动/停止本地 v1 单机栈的进程与日志 -->
<!-- Pos: 运维脚本目录索引（变更时同步更新以上注释与本文件内容） -->

# `scripts/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- `start.sh`：`docker compose --profile app up -d --build` 启动全栈容器（含 `bootstrap` 一次性引导主数据）；GPU 模式可选在宿主机启动 vLLM（Docker 外）并在等待就绪时输出 vLLM 日志；启动后会提示 `a_share_basic` 是否仍为空。
- `stop.sh`：`docker compose down` 停止本仓库全栈容器，并尽力停止宿主机 vLLM（优先 `.run/vllm.pid`，并可按端口探测兜底）。
- `start.ps1`：Windows PowerShell 一键启动（对齐 `start.sh`；含 `bootstrap` 引导与空库提示；GPU 模式下启动宿主机 vLLM，并在等待就绪时持续输出 vLLM 日志）。
- `stop.ps1`：Windows PowerShell 停止 `docker compose down`，并尽力停止宿主机 vLLM。
- `start.cmd`/`stop.cmd`：Windows 双击入口（调用对应 `.ps1`）。
- 运行态输出：`start.*` 会同步打印 `docker compose logs` 并落盘（默认 `var/log/compose.log`；可用 `TXNEWS_COMPOSE_LOG_FILE` 覆盖；`TXNEWS_FOLLOW_LOGS=0` 只打印 tail 并退出）。
- 权限提示：若当前 shell 无法访问 Docker daemon（`/var/run/docker.sock`），`start.sh` 会报错中止，`stop.sh` 会提示并跳过停止容器（仍会停止宿主机 vLLM）。
- 国内拉取加速：`setup_docker_mirror.sh` 可写入 Docker daemon `registry-mirrors` 配置（需要 root），用于加速基础镜像 `docker pull`。
- 在线 LLM 排障：`check_online_llm.sh` 用 `.env` 的在线 LLM 配置做“宿主机 vs 容器”连通性对比（DNS/TLS/HTTP/SSE，并做 IPv4(-4)/IPv6(-6) 对比，打印 resolv.conf 与 proxy/no_proxy；用于定位 `_ssl.c:999 handshake timed out`）。
- 爬虫排障：`check_crawler_connectivity.sh` 对 `config/sources.txt` 做“宿主机 vs collector 容器”的 DNS/TLS/HTTP 探测，并在容器侧额外检测 NATS JetStream 与基础设施端口连通性（用于定位 `_ssl.c:999 handshake timed out`、`nats: no response from stream`、容器内 DNS 异常等）。
- 本地非 Docker 启动：`start_local.sh`/`stop_local.sh` 用 docker 只起基础设施（默认 `docker compose up -d`），然后在宿主机 venv 里启动 api/worker/beat/collector/nats-bridge/admin（可用 `--no-admin` 关闭）；`start_local.sh` 会自动创建 venv 并默认 `pip install -r requirements.txt`，并对 docker 版 .env 的 infra 域名做本地替换（postgres/redis/nats/minio/qdrant → localhost），GPU 模式可选启动宿主机 vLLM；默认 `--web-dev` 在 8000 启动 Vite 热更新（API 自动挪到 `TXNEWS_LOCAL_API_PORT`，默认 18000），必要时会补齐 npm 依赖；`stop_local.sh` 默认会停止宿主机进程（含 vLLM）并关闭由 start_local 启动的 infra（可用 `--keep-infra` 保留容器）。
- 演示/回归脚本：`demo_kb_api_test.py`（知识库 API：/status + /search）、`demo_db_api_test.py`（数据库 API：/status + /signals + /articles/{id}）。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `start.sh` | 运维入口 | 一键启动 Docker 版本全栈（profile=app；GPU 模式可选启动宿主机 vLLM）。 |
| `stop.sh` | 运维入口 | 停止 Docker 版本全栈（profile=app；并尽力停止宿主机 vLLM）。 |
| `start.ps1` | 运维入口 | Windows PowerShell 一键启动 Docker 版本全栈（启动后会持续输出容器日志并落盘）。 |
| `stop.ps1` | 运维入口 | Windows PowerShell 停止 Docker 版本全栈。 |
| `start.cmd` | 运维入口 | Windows 双击启动（调用 `start.ps1`）。 |
| `stop.cmd` | 运维入口 | Windows 双击停止（调用 `stop.ps1`）。 |
| `setup_docker_mirror.sh` | 运维辅助 | 配置 Docker daemon registry mirror（加速 `docker pull`；需要 root）。 |
| `check_online_llm.sh` | 运维排障 | 在线 LLM 连通性检查（host vs api 容器）。 |
| `check_crawler_connectivity.sh` | 运维排障 | 爬虫 sources 连通性检查（host vs collector 容器；含 NATS/infra TCP）。 |
| `start_local.sh` | 运维入口 | docker 仅启动基础设施；宿主机 venv 启动主程序（api/worker/beat/collector/nats-bridge/admin）、db-init/bootstrap，GPU 模式可选启动宿主机 vLLM；默认 8000 Vite dev。 |
| `stop_local.sh` | 运维入口 | 停止 `start_local.sh` 启动的宿主机进程（含 vLLM）；可选 `--keep-infra` 保留 infra 容器。 |
| `demo_kb_api_test.py` | Demo/测试 | 在服务运行中调用 `/search` 并跟进 `/articles/{canonical_id}`，验证知识库检索链路可用（不写入任何测试数据）。 |
| `demo_db_api_test.py` | Demo/测试 | 在服务运行中调用 `/status`、`/signals`、`/articles/{canonical_id}`（可选强制非空），验证数据库读路径可用。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
