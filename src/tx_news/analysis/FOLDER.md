<!-- Input: 新闻文本/标题、配置、（可选）DashScope API Key -->
<!-- Output: 事件类型/实体/影响等结构化分析结果 -->
<!-- Pos: 分析层索引（变更时同步更新以上注释与本文件内容） -->

# `src/tx_news/analysis/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- `rules.py` 提供规则分类与事件窗口逻辑（无外部依赖）。
- `dashscope.py` 封装 OpenAI 兼容的 chat/json 接口（在线 LLM，可选；可用于 DashScope/其他兼容服务）。
- `tickers.py` 做 A 股名称→ts_code 的朴素匹配，辅助分析。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包标识 | 标记分析子包。 |
| `rules.py` | 规则引擎 | event_type 分类、event_id 与窗口规划。 |
| `tickers.py` | 实体匹配 | 名称匹配生成 ticker 实体列表。 |
| `dashscope.py` | LLM 客户端 | DashScope chat/json 请求封装。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
