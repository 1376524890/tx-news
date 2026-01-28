<!-- Input: 现有实现（pipeline/analyze/deep_analysis/kg/graph API）与配置 -->
<!-- Output: 分析图谱网络的搭建/迭代流程说明 -->
<!-- Pos: 分析图谱网络构建文档（变更时同步更新以上注释与 docs/FOLDER.md） -->

# 分析图谱网络搭建与迭代流程

> 面向当前代码实现的说明文档；描述“分析图谱”从新闻入库到图谱更新的完整链路与迭代机制。

## 1. 范围与术语

本项目存在两类“图谱/网络”视图：

1) **v2 KG Memory（Qdrant）**
- 存储在 Qdrant 的 `txnews_event_memory` / `txnews_entity_memory` / `txnews_edge_memory` 三类 collection。
- 节点/边含 embedding，用于后续检索或迭代治理。

2) **Dashboard 近实时图谱（API 端聚合）**
- `/kg/graph` 接口直接从 Postgres `analyses` 里按时间窗口聚合事件与 ticker，生成可视化用的“事件-股票”二部图。
- 与 Qdrant KG 是两条并行输出路径，目的不同：可视化 vs 长期记忆。

本文重点描述“分析图谱网络”的搭建与迭代，覆盖两条路径的来源与更新点。

## 2. 数据流入口：采集 → 归一 → 去重 → 入队

入口任务位于 `src/tx_news/tasks/pipeline.py`：

1) `ingest_raw` → `normalize_raw`：抓取内容归一化（标题/正文/时间等）。
2) `dedup_store`：
   - LSH + 语义向量去重，生成 canonical 文章。
   - 新 canonical 会写入 `articles`/`article_versions`。
   - **新文章会进入分析优先队列**（`news_queue.add_to_queue`），为后续分析触发做准备。

关键点：
- 只有 **新 canonical** 会入队分析，避免重复消耗。
- 分析队列由 Redis 管理（见 `src/tx_news/tasks/news_queue.py`）。

## 3. 分析任务：事件识别与结构化分析

队列工人 `queue_worker_task`（celery beat 每分钟触发）从队列取出 canonical 并触发 `pipeline.analyze`。

`pipeline.analyze` 的核心流程：

1) **事件类型识别**：`analysis.rules.classify_event_type`（关键词规则）。
2) **事件窗口归并**：`EventWindowPlanner` 根据 `config/config.yaml` 的 `event_windows_minutes` 计算窗口起点。
3) **事件 ID**：`stable_event_id(event_type, key_entity, window_start)` 生成稳定 event_id。
4) **Ticker/实体识别**：`TickerMatcher` 基于 A 股主数据进行匹配。
5) **分析结果写回**：写 `analyses`（含 `event_id`、`tickers`、`impact`、`evidence` 等）。
6) **发信号**：插入 `signals`，kind=`analysis_updated`。
7) **触发 KG 更新**：异步调用 `kg_update_from_canonical`。

> 说明：分析阶段会优先尝试 LLM（本地 vLLM 或线上 LLM），失败则回退到规则输出。

## 4. 深度分析：二次推理与修正

`deep_analysis.deep_optimize` 在以下条件触发：
- canonical 是新文章（`is_new_canonical=True`）。
- `llm.deep` 可用（本地 vLLM 或已配置 key）。

流程概述：
1) 用文章向量从 Qdrant 检索相似新闻作为证据。
2) 结合初步分析 + 相似证据，调用 LLM 输出修正结果。
3) 更新 `analyses`，新增字段 `deep_optimized_at`。
4) 插入信号 `deep_analysis_updated`。
5) **再次触发 KG 更新**，以修正事件节点/边。

> 深度分析使图谱具备“迭代修正”能力（更新事件类型/实体/影响等）。

## 5. v2 KG Memory 的构建与迭代（Qdrant）

核心入口：`tx_news.tasks.kg.kg_update_from_canonical`。

### 5.1 构建计划（Planner）
基于 `analyses` 生成 GraphOpsPlan（见 `tx_news.kg.graphops`）：

- **Ticker 节点**：`node_id_ticker(ts_code)`，payload 包含名称/行业、最近事件与链接。
- **Event 节点**：`node_id_event(event_id)`，payload 包含 `snapshot_text`（由 `snapshot.build_event_snapshot_text` 规则生成）。
- **mentions 边**：事件 ↔ 股票，根据最近 12 篇同事件新闻计数建边。
- **related_to 边**：在事件窗口内寻找相邻事件，依据 ticker overlap + event_type bonus 打分（`kg.scoring`）。

所有节点/边先写入 **sandbox** 环境（graph_env=sandbox）。

### 5.2 校验与执行
- 计划会先 `validate_plan` 确保 payload 与 evidence 完整。
- executor 生成 embedding（若 plan 未提供 vector）并写入 Qdrant。

### 5.3 Sandbox → Prod 提交
- 通过固定的“critic”（当前为确定性 pass）后提交 prod。
- prod 写入前会执行 **snapshot_before**，用于回滚。

### 5.4 运行审计与回滚
- `KGRun`/`KGOpsLog`：记录 planner/validator/executor/critic 的完整轨迹。
- `KGSnapshot`：记录 prod 修改前的快照（支持 `kg_rollback(snapshot_id)` 回滚）。

### 5.5 迭代治理任务
- `kg_gc`：周期性删除/衰减边（控制边质量与时效）。
- `kg_reconcile`：每日重建最近 24h 事件的 snapshot_text 与向量。

## 6. Dashboard 图谱（API 聚合）

`/kg/graph` 接口基于 `analyses` 表实时聚合：

- **事件节点**：按 event_id 归并，统计事件内文章数与反馈分。
- **Ticker 节点**：统计 ticker 出现频次。
- **边**：事件-股票 mentions，基于同事件文章计数并叠加反馈权重。
- **反馈**：来自 `feedback_logs`（thumb_up/down），用于调整可视化权重。

> 该视图不依赖 Qdrant KG，适用于实时看板展示。

## 7. 关键数据落点

- Postgres：`articles` / `article_versions` / `analyses` / `signals` / `kg_runs` / `kg_ops_logs` / `kg_snapshots`。
- Qdrant：`txnews_event_memory` / `txnews_entity_memory` / `txnews_edge_memory`（包含 sandbox/prod 双环境点）。
- Redis：分析/图谱锁与分析队列。

## 8. 代码入口索引

- 采集与分析流水线：`src/tx_news/tasks/pipeline.py`
- 分析队列：`src/tx_news/tasks/news_queue.py`
- 深度分析：`src/tx_news/tasks/deep_analysis.py`
- KG 构建与治理：`src/tx_news/tasks/kg.py`
- KG 结构与规则：`src/tx_news/kg/*`
- Dashboard 图谱 API：`apps/api/main.py` (`/kg/graph`)

## 9. 常见迭代路径（概览）

1) 新文章 → 入队 → analyze → KG 更新。
2) deep_analysis 触发后 → 修正分析 → KG 二次更新。
3) 每日 reconcile / 周期 GC → 修整 KG 内部结构。
4) 看板查询 → 聚合 analyses 形成前端图谱。

如需扩展图谱（新增节点类型/边类型/权重规则），建议同步修改：
- `tx_news.kg.graphops`/`scoring`/`snapshot`
- `kg_update_from_canonical` 中的 Planner 逻辑
- 若涉及 UI 展示，更新 `/kg/graph` 聚合逻辑
