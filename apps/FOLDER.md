<!-- Input: apps/ 下的运行入口与子模块 -->
<!-- Output: apps/ 目录结构索引与文件职责 -->
<!-- Pos: apps/ 目录索引文档（变更时同步更新以上注释与本文件内容） -->

# `apps/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 运行入口层：把 `src/tx_news/` 能力组装成进程/服务（`python -m ...`）。
- 主要用于本地/部署运行，不作为复用 API 的放置位置。
- 子目录按进程拆分：API / Collector / Worker / MCP。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包标识 | 标记 `apps` 为 Python 包。 |
| `sync_tushare.py` | 手动入口 | 触发 A 股主数据同步（调用 Celery 任务函数）。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |

## 子目录

| 子目录 | 地位 | 功能 |
| --- | --- | --- |
| `api/` | HTTP 服务 | FastAPI + 静态 UI。 |
| `collector/` | 采集进程 | 拉取来源并发布 raw 事件。 |
| `worker/` | 处理进程 | NATS→Celery 桥接与任务执行。 |
| `mcp/` | 工具服务 | stdio JSON-RPC/MCP 工具接口。 |
