<!-- Input: NATS raw payload、配置、外部服务（DB/向量库/LLM） -->
<!-- Output: Celery 任务链执行结果、DB upsert、signals -->
<!-- Pos: 异步任务与流水线编排索引（变更时同步更新以上注释与本文件内容） -->

# `src/tx_news/tasks/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- `celery_app.py` 定义 Celery app 与任务注册策略（并包含 v2.2 所需的 beat_schedule；并默认抑制 httpx/qdrant 的 HTTP Request 噪声日志）。
- `pipeline.py` 定义 Raw→Normalize→Dedup 的主流水线任务（近重复 + 语义去重均按 `TXNEWS_DEDUP_WINDOW_HOURS` 只对比窗口内文章），并在 `dedup_store` 完成后显式入队 `analyze`（避免链式回调在 broker/DNS 抖动时丢失导致“分析不推进”）；同时对 `tickers` 等字段做 schema 归一化，保证 API/UI 稳定；并对相同 checksum 的 canonical 分析做幂等跳过，避免重复分析/重复 LLM 成本。
- `news_queue.py` 维护分析优先队列，并由 Celery 定时 `queue_worker_task` 拉取队列触发 `pipeline.analyze`（队列工人对新入库 canonical 标记 `is_new_canonical`，确保深分析能被触发）。
- `deep_analysis.py` 与 `maintenance.py` 提供“深分析”和“维护任务”（GPU 模式下 `pipeline.analyze()` 与深分析均优先使用 `llm.deep` 指向本地 vLLM；不可用时回退到 `llm.chat`（需配置 api_key）；embedding 支持绑定到指定 GPU，并在换模型/维度变化时自动兼容 Qdrant collection）。
- `kg.py` 提供 v2 KG 增量更新与治理任务：`kg_update_from_canonical`（sandbox→prod）、`kg_gc`（边 GC/衰减）、`kg_reconcile`（事件快照重算）、`kg_rollback`（回滚）与审计/快照写入。
- `causal.py` 提供 v3 因果合成任务：从分析结果生成变量边与因果边（sandbox→prod），支持回滚。
- 向量化：canonical 的 embedding 以“抽取后的正文文本”为输入（不再额外做字符级截断；实际长度仍可能受 embedding 模型的最大 token 限制影响）。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包标识 | 标记 tasks 子包。 |
| `celery_app.py` | Celery 配置 | broker/backend/路由与任务注册。 |
| `pipeline.py` | 主流水线 | normalize/dedup/analyze/ingest_raw 等任务。 |
| `news_queue.py` | 队列与工人 | 分析优先队列与定时 `queue_worker_task` 处理逻辑。 |
| `deep_analysis.py` | 深分析 | 相似检索 + LLM 二次推理并回写。 |
| `maintenance.py` | 维护任务 | 主数据同步与 raw TTL 清理。 |
| `kg.py` | v2 KG | KG 增量更新（sandbox→prod）、边 GC/衰减、事件快照重算与回滚。 |
| `causal.py` | v3 因果 | 变量/因果边合成与回滚。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
