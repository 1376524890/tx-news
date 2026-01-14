<!-- Input: LLM 调用 + 本地工具查询（Qdrant/Postgres） -->
<!-- Output: 面向 UI/API 的结构化对话回答与证据列表 -->
<!-- Pos: Agent 子系统索引（变更时同步更新以上注释与本文件内容） -->

# `src/tx_news/agent/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- `TxNewsAgent`：围绕“先检索证据再回答”的工具循环。
- `TxNewsTools`：提供 search/list/timeline/profile 等可调用工具（主数据缺失时可从本地缓存引导）。
- 底层依赖 `storage/` 与 `embedding/`（embedding 默认强制 CPU；可用 `TXNEWS_EMBEDDING_DEVICE` 覆盖），上层由 `apps/api` 调用；当在线 LLM SSE 不稳定时可自动回退到非流式以保证 `/chat/stream` 可用。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包入口 | 导出 `TxNewsAgent`。 |
| `txnews_agent.py` | Agent 核心 | LLM 对话 + 工具调用循环 + 证据收集；`run_stream` 额外提供工具调用进度事件（tool_call/tool_result）。 |
| `tools.py` | 工具实现 | 向量检索/近时列表/时间线/主数据查询。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
