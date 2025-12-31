<!-- Input: NATS raw payload、配置、外部服务（DB/向量库/LLM） -->
<!-- Output: Celery 任务链执行结果、DB upsert、signals -->
<!-- Pos: 异步任务与流水线编排索引（变更时同步更新以上注释与本文件内容） -->

# `src/tx_news/tasks/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- `celery_app.py` 定义 Celery app 与任务注册策略。
- `pipeline.py` 定义 Raw→Normalize→Dedup→Analyze 的主流水线任务（并对 `tickers` 等字段做 schema 归一化，保证 API/UI 稳定）。
- `deep_analysis.py` 与 `maintenance.py` 提供“深分析”和“维护任务”（深分析使用 `llm.deep` 可指向本地 vLLM；`pipeline.analyze()` 读取 `llm.chat` 保持云端；embedding 支持绑定到指定 GPU，并在换模型/维度变化时自动兼容 Qdrant collection）。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包标识 | 标记 tasks 子包。 |
| `celery_app.py` | Celery 配置 | broker/backend/路由与任务注册。 |
| `pipeline.py` | 主流水线 | normalize/dedup/analyze/ingest_raw 等任务。 |
| `deep_analysis.py` | 深分析 | 相似检索 + LLM 二次推理并回写。 |
| `maintenance.py` | 维护任务 | 主数据同步与 raw TTL 清理。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
