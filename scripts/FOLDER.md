<!-- Input: 本地环境（Python/Docker）与仓库配置文件 -->
<!-- Output: 启动/停止本地 v1 单机栈的进程与日志 -->
<!-- Pos: 运维脚本目录索引（变更时同步更新以上注释与本文件内容） -->

# `scripts/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- `start.sh`：构建前端（public/admin 两套产物）→ 准备 `.venv`/依赖（可自动安装匹配的 torch）→ 预检 embedding（总是使用 CPU）→ 启动 Docker infra →（可选）启动本地 vLLM → 启动后台进程（8000 API + 8001 配置服务）并做健康检查。
- `stop.sh`：停止 `.run/*.pid` 记录的后台进程（不自动 `docker compose down`）。
- `start.ps1`：Windows PowerShell 一键启动（尽量对齐 `start.sh`：前端构建、torch 自动安装、embedding 预检、启动 Docker infra、仅 `TXNEWS_ACCELERATOR=gpu` 时尝试启动 vLLM 并做 `/health`/`/v1/models` 检查；vLLM 需要 WSL 或 Git-Bash）。
- `stop.ps1`：Windows PowerShell 停止 `.run/*.pid` 记录的后台进程。
- `start.cmd`/`stop.cmd`：Windows 双击入口（调用对应 `.ps1`）。
- 运行态输出：日志在 `var/log/`，PID 在 `.run/`。
- 演示/回归脚本：`demo_kb_api_test.py`（知识库 API：/status + /search）、`demo_db_api_test.py`（数据库 API：/status + /signals + /articles/{id}）。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `start.sh` | 运维入口 | 一键启动本地开发/演示环境。 |
| `stop.sh` | 运维入口 | 停止由 `start.sh` 启动的后台进程。 |
| `start.ps1` | 运维入口 | Windows PowerShell 一键启动（尽量对齐 `start.sh`；仅 `TXNEWS_ACCELERATOR=gpu` 才尝试启动 vLLM，且需 WSL 或 Git-Bash）。 |
| `stop.ps1` | 运维入口 | Windows PowerShell 停止由 `start.ps1` 启动的后台进程。 |
| `start.cmd` | 运维入口 | Windows 双击启动（调用 `start.ps1`）。 |
| `stop.cmd` | 运维入口 | Windows 双击停止（调用 `stop.ps1`）。 |
| `demo_kb_api_test.py` | Demo/测试 | 在服务运行中调用 `/search` 并跟进 `/articles/{canonical_id}`，验证知识库检索链路可用（不写入任何测试数据）。 |
| `demo_db_api_test.py` | Demo/测试 | 在服务运行中调用 `/status`、`/signals`、`/articles/{canonical_id}`（可选强制非空），验证数据库读路径可用。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
