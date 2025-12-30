<!-- Input: 本地环境（Python/Docker）与仓库配置文件 -->
<!-- Output: 启动/停止本地 v0 单机栈的进程与日志 -->
<!-- Pos: 运维脚本目录索引（变更时同步更新以上注释与本文件内容） -->

# `scripts/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- `start.sh`：准备 `.venv`/依赖（可自动安装匹配的 torch）→ 预检 embedding（按 `.env` 的 CPU/GPU 选择）→ 启动 Docker infra →（可选）启动本地 vLLM → 启动后台进程并做健康检查。
- `stop.sh`：停止 `.run/*.pid` 记录的后台进程（不自动 `docker compose down`）。
- `start.ps1`：Windows PowerShell 一键启动（功能对齐 `start.sh`）。
- `stop.ps1`：Windows PowerShell 停止 `.run/*.pid` 记录的后台进程。
- `start.cmd`/`stop.cmd`：Windows 双击入口（调用对应 `.ps1`）。
- 运行态输出：日志在 `var/log/`，PID 在 `.run/`。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `start.sh` | 运维入口 | 一键启动本地开发/演示环境。 |
| `stop.sh` | 运维入口 | 停止由 `start.sh` 启动的后台进程。 |
| `start.ps1` | 运维入口 | Windows PowerShell 一键启动（对齐 `start.sh`）。 |
| `stop.ps1` | 运维入口 | Windows PowerShell 停止由 `start.ps1` 启动的后台进程。 |
| `start.cmd` | 运维入口 | Windows 双击启动（调用 `start.ps1`）。 |
| `stop.cmd` | 运维入口 | Windows 双击停止（调用 `stop.ps1`）。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
