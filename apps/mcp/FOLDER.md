<!-- Input: stdin JSON-RPC 请求 + 本地数据库/向量库 -->
<!-- Output: stdout JSON-RPC 响应（tools/list, tools/call 等） -->
<!-- Pos: MCP 工具服务目录索引（变更时同步更新以上注释与本文件内容） -->

# `apps/mcp/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 通过 stdio 提供最小 MCP/JSON-RPC 工具服务（无需 HTTP）。
- 工具底层复用 Qdrant（向量检索）与 Postgres（元数据/分析结果）。
- 支持可选返回全文的工具（默认关闭，需设置 `TXNEWS_ALLOW_FULL_TEXT=1`）。
- 工具检索同样使用本地 embedding 配置（默认强制 CPU；可用 `TXNEWS_EMBEDDING_DEVICE` 覆盖），并在向量维度变化时自动兼容 Qdrant collection。
 - 当 `a_share_basic` 为空时，`get_entity_profile` 会尝试从本地缓存引导一次，减少首次部署的空库问题。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包标识 | 标记 `apps.mcp` 为包。 |
| `server.py` | 服务入口 | 解析 JSON-RPC，分发工具调用并输出结果。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
