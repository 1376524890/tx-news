<!-- Input: src/tx_news 下的核心库代码结构 -->
<!-- Output: 核心库分层说明与文件职责清单 -->
<!-- Pos: tx_news 核心库目录索引（变更时同步更新以上注释与本文件内容） -->

# `src/tx_news/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 核心可复用库：采集、清洗、去重、向量、存储、任务、Agent。
- 上层 `apps/*` 负责把能力组装成进程/服务；这里避免放运行脚本。
- 数据流抽象：Raw → Normalize → Dedup/Embed → Analyze → Store/Signal。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包入口 | 包版本与导出符号。 |
| `db.py` | ORM 模型 | SQLAlchemy 表结构与关系定义。 |
| `models.py` | DTO 模型 | Pydantic 数据结构（Raw/Normalized/Canonical/Analysis）。 |
| `settings.py` | 配置入口 | 环境变量（含 OpenAI 兼容 LLM 参数）+ `config/` 文件加载与默认值。 |
| `logging.py` | 基础设施 | 统一日志格式与等级配置。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |

## 子目录

| 子目录 | 地位 | 功能 |
| --- | --- | --- |
| `agent/` | 对话 Agent | 基于工具检索的新闻问答/分析助手。 |
| `analysis/` | 分析层 | 规则分类、ticker 匹配、DashScope 客户端。 |
| `bus/` | 事件总线 | NATS JetStream 发布/建流封装。 |
| `crawler/` | 采集层 | HTTP 抓取与 root→link→raw 流程。 |
| `dedup/` | 去重层 | MinHash LSH 近重复检索与索引持久化。 |
| `embedding/` | 向量层 | embedding 模型加载与向量化。 |
| `integrations/` | 外部集成 | Tushare/AkShare 同步与缓存。 |
| `normalize/` | 清洗层 | Readability 抽取正文、发布时间解析。 |
| `storage/` | 存储层 | Postgres/MinIO/Qdrant 访问封装。 |
| `tasks/` | 任务编排 | Celery app 与 pipeline/deep/maintenance 任务。 |
