<!-- Input: 本地环境（Python/Docker）与仓库配置文件 -->
<!-- Output: 启动/停止本地 v0 单机栈的进程与日志 -->
<!-- Pos: 运维脚本目录索引（变更时同步更新以上注释与本文件内容） -->

# `scripts/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- `start.sh`：准备 `.venv`/依赖 → 启动 Docker infra → 启动后台进程（worker/bridge/collector/api）。
- `stop.sh`：停止 `.run/*.pid` 记录的后台进程（不自动 `docker compose down`）。
- 运行态输出：日志在 `var/log/`，PID 在 `.run/`。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `start.sh` | 运维入口 | 一键启动本地开发/演示环境。 |
| `stop.sh` | 运维入口 | 停止由 `start.sh` 启动的后台进程。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
