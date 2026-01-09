<!-- Input: apps/api 下的 FastAPI 代码 -->
<!-- Output: API 服务与 Vue SPA 静态挂载 -->
<!-- Pos: apps/api 目录索引文档 -->

# `apps/api/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- FastAPI 对外提供检索/对话等用户侧 HTTP 接口（8000）。
- 静态挂载 `apps/web/dist_public` 作为用户侧 UI（对话 `/`、看板 `/dashboard`、配置 `/config`；不提供运维管理台 UI）。
- LLM：默认读取 `llm.chat`；也支持从 cookie 用户标识在 Redis 中读取“个人在线 LLM 配置”，用于按用户分摊 chat 成本。

补充：
- `/chat/stream` 使用 SSE 逐步输出 `delta`（文本增量）、`tool`/`tool_result`（工具调用进度）与 `done`（最终消息）。
- embedding 配置通过 `Settings.resolve_embedding_cfg()` 统一解析：默认强制使用 CPU（除非显式设置 `TXNEWS_EMBEDDING_DEVICE`），避免在 API/工具侧出现“配置不一致导致 500”。
- 知识库全文（内部）：提供 `/kb/search` 与 `/kb/articles/{canonical_id}` 返回抽取后的全文（默认关闭，需设置 `TXNEWS_ALLOW_FULL_TEXT=1`）。
- `/status`、`/signals`、`/dashboard/summary`、`/api/config` 默认返回 `Cache-Control: no-store`，避免被 Cloudflare Tunnel/反代缓存导致“长时间不更新”。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包标识 | 标记 `apps.api` 为包。 |
| `main.py` | 服务入口 | FastAPI 路由、依赖健康检查、Vue SPA 挂载。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |

## 子目录

| 子目录 | 地位 | 功能 |
| --- | --- | --- |
| `static/` | Legacy UI | 旧版静态文件 (已由 `apps/web` 替代)。 |
