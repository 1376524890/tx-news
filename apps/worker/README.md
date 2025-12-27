# Worker processes

This repo uses:

- `Celery` workers to run the pipeline tasks
- a small NATS JetStream bridge that consumes `txnews.raw` messages and enqueues Celery tasks

