<!-- Input: NATS JetStream 的 raw 消息 + Redis(Celery broker) -->
<!-- Output: 触发 tx_news.tasks.pipeline 的 Celery 任务链 -->
<!-- Pos: Worker/Bridge 进程目录索引（变更时同步更新以上注释与本文件内容） -->

# `apps/worker/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- `nats_bridge.py` 消费 `txnews.raw` 并 `ingest_raw.delay()` 入队。
- Celery worker 执行 `normalize_raw → dedup_store → analyze` 等任务（见 `src/tx_news/tasks/`）。
- 该目录只提供桥接与说明，不承载具体业务逻辑实现。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `README.md` | 说明文档 | 简述 worker 与桥接的角色。 |
| `__init__.py` | 包标识 | 标记 `apps.worker` 为包。 |
| `nats_bridge.py` | 进程入口 | NATS JetStream consumer → Celery 入队。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
