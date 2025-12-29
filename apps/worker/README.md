<!-- Input: Worker/Bridge 相关背景与术语 -->
<!-- Output: worker 目录的最小说明 -->
<!-- Pos: worker 文档（变更时同步更新以上注释与所属目录 FOLDER.md） -->

# Worker processes

This repo uses:

- `Celery` workers to run the pipeline tasks
- a small NATS JetStream bridge that consumes `txnews.raw` messages and enqueues Celery tasks
