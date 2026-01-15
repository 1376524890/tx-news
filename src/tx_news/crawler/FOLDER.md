<!-- Input: 来源 URL、HTTP 返回内容、基础配置 -->
<!-- Output: raw 抓取结果、链接提取结果、raw 事件发布 -->
<!-- Pos: 采集层索引（变更时同步更新以上注释与本文件内容） -->

# `src/tx_news/crawler/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- `fetcher.py` 负责 HTTP 拉取与 root 页面链接提取。
- `collector.py` 负责 root→正文抓取、写入存储、发布 NATS raw 事件；单个来源/发布失败不应导致进程退出。
- 该层不做分析，只产出可追溯的 raw 与元数据。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包标识 | 标记 crawler 子包。 |
| `fetcher.py` | 抓取组件 | `Fetcher.fetch()` 与 `extract_links()`。 |
| `collector.py` | 采集编排 | 抓取/落盘/发布 raw 消息的 orchestrator。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
