<!-- Input: config/sources.txt 与 config/config.yaml 等配置 -->
<!-- Output: MinIO raw 对象 + NATS raw 事件消息 -->
<!-- Pos: Collector 进程目录索引（变更时同步更新以上注释与本文件内容） -->

# `apps/collector/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 周期性抓取来源列表：root 页提取链接 → 抓正文。
- raw 内容写入 MinIO（S3），元数据写入 Postgres，并发布到 NATS JetStream。
- 单机 v0：每 60 秒跑一轮（见 `main.py`）。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包标识 | 标记 `apps.collector` 为包。 |
| `main.py` | 进程入口 | 初始化配置与依赖，循环执行 `Collector.run_once()`。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
