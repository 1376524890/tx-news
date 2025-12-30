<!-- Input: apps/api 下的 FastAPI 代码 -->
<!-- Output: API 服务与 Vue SPA 静态挂载 -->
<!-- Pos: apps/api 目录索引文档 -->

# `apps/api/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- FastAPI 对外提供检索/对话/管理台等 HTTP 接口。
- 静态挂载 `apps/web/dist` (Vue 3 SPA) 作为前端 UI。
- 依赖 Postgres/Qdrant/Redis/NATS（通过 `tx_news.settings` 读取连接信息）。

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

