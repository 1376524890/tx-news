<!-- Input: Postgres/MinIO/Qdrant 连接信息与读写请求 -->
<!-- Output: 数据库/对象存储/向量库的读写结果 -->
<!-- Pos: 存储层索引（变更时同步更新以上注释与本文件内容） -->

# `src/tx_news/storage/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- `postgres.py`：SQLAlchemy 访问封装（Engine 进程内缓存，避免长跑时连接数膨胀；建表、CRUD、查询；反馈汇总；并提供主数据缺失时的本地缓存引导；Session 默认 `expire_on_commit=False` 便于 API/工具层安全读取）。
- `minio.py`：S3 API 封装（raw 内容存取/删除；实现侧对 `boto3` 做延迟导入以便单测环境可导入）。
- `qdrant.py`：向量存储封装（upsert/search/retrieve/scroll/delete + collection 初始化）；兼容 `qdrant-client` 的 `query_points/search/scroll` API；point id 使用确定性 UUID（避免服务端不接受字符串 id）。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包标识 | 标记 storage 子包。 |
| `postgres.py` | OLTP 存储 | Postgres 访问与事务会话封装。 |
| `minio.py` | 对象存储 | MinIO/S3 读写 raw bytes。 |
| `qdrant.py` | 向量存储 | Qdrant collection 管理与检索。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
