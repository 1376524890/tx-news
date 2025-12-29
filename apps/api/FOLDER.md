<!-- Input: apps/api 下的 FastAPI 代码与静态资源 -->
<!-- Output: API 服务目录结构索引与文件职责 -->
<!-- Pos: apps/api 目录索引文档（变更时同步更新以上注释与本文件内容） -->

# `apps/api/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- FastAPI 对外提供检索/对话/管理台等 HTTP 接口，并挂载 `static/` UI。
- 依赖 Postgres/Qdrant/Redis/NATS（通过 `tx_news.settings` 读取连接信息）。
- 对话支持 `/chat/stream` SSE 流式输出；检索使用本地 embedding 配置并自动兼容 Qdrant collection。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包标识 | 标记 `apps.api` 为包。 |
| `main.py` | 服务入口 | FastAPI 路由、依赖健康检查、UI 挂载。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |

## 子目录

| 子目录 | 地位 | 功能 |
| --- | --- | --- |
| `static/` | 前端静态资源 | `index.html`/`admin.html` + JS/CSS。 |
