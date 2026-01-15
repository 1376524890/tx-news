<!-- Input: v1 现状（collector→worker→Postgres/Qdrant）+ 你的决策（v2.2 一次性全量交付；实体仅 ticker；边存 Qdrant；UI 反馈闭环；单一权威实施文档） -->
<!-- Output: 可直接按步骤实施的 v2.2 开发清单（包含数据结构、任务、API、UI、验收与回滚），适合 AI 逐条执行 -->
<!-- Pos: v2 实施权威文档（变更时同步更新以上注释与 docs/FOLDER.md；并同步更新涉及目录的 FOLDER.md） -->

# TX-News v2.2 一次性交付实施文档（可执行步骤）

本文件是 v2 的唯一权威实施指南。目标：在 v1 采集/去重/分析/向量检索基础上，一次性完成 **v2.2（自连接 + 自迭代治理 + 反馈闭环）** 的全部开发，并在现有 `/dashboard` 页面新增 **实时 3D 知识图谱** 可视化。

固定决策（不可变更）：
- 实体类型：仅 `ticker/company`（A 股 `ts_code`）。
- 边存储：Qdrant `txnews_edge_memory`（边作为可检索对象 + 可解释 reason_text）。
- 存储策略：Qdrant 为主检索（event/entity/edge memory）；Postgres 仅做审计/回滚/反馈日志。
- UI 反馈闭环：前端上报点击/赞踩；后端落 Postgres `feedback_logs`。
- 交付一次完成：不保留“可选/建议/二选一/必要时”分支。

---

## 0. 基线与统一约束（先读）

代码落点（固定；按此创建/修改文件）：
- Postgres ORM：`src/tx_news/db.py`
- Postgres DAO：`src/tx_news/storage/postgres.py`
- Qdrant 封装：`src/tx_news/storage/qdrant.py`
- KG 逻辑：`src/tx_news/kg/`（新建，包含规则生成/相似度/GraphOps schema）
- KG 任务：`src/tx_news/tasks/kg.py`（新建）
- Celery 注册：`src/tx_news/tasks/celery_app.py`（加入 include 与 route）
- API：`apps/api/main.py`（新增 /kg/graph 与 /feedback；新增 v2 工具接口时也放这里）
- 前端 3D 组件：`apps/web/src/components/KG3DGraph.vue`（已规划）
- 看板页：`apps/web/src/views/DashboardView.vue`（集成 3D 组件）

统一约束（写代码时必须满足）：
- 不输出新闻原文；UI/接口仅返回 URL + 短摘要/结构化字段。
- 任意“关系/边”必须可追溯：`evidence_canonical_ids` 非空。
- 所有写入任务幂等：同一 `canonical_id` 重复触发不会产生重复节点/边。
- 所有 v2 写入都以 `graph_env=prod` 为默认环境；同时实现 `sandbox` 隔离并可回滚。

统一命名（全仓库一致）：
- Node ID：
  - Event 节点：`event:{event_id}`
  - Ticker 节点：`ticker:{ts_code}`
- Edge ID：`edge:{src}|{relation}|{dst}`（用于生成确定性 uuid5 point id）

---

## 1. 数据结构（必须先落地）

### 1.1 Qdrant collections（固定 3 个）

1) `txnews_event_memory`
- point_id：`event:{event_id}`
- vector：`embedding(snapshot_text)`
- payload（最小且固定）：
  - `node_type`: `"event"`
  - `event_id`: string
  - `event_type`: string
  - `snapshot_text`: string（<= 2000 chars，规则版生成）
  - `canonical_ids`: string[]（证据 canonical_id 列表，最多 64）
  - `last_published_at_ts`: int（unix seconds）
  - `updated_at_ts`: int（unix seconds）
  - `graph_env`: `"prod"|"sandbox"`

2) `txnews_entity_memory`
- point_id：`ticker:{ts_code}`
- vector：`embedding(description_text)`
- payload：
  - `node_type`: `"ticker"`
  - `ts_code`: string
  - `name`: string
  - `industry`: string|null
  - `description_text`: string（<= 1200 chars）
  - `updated_at_ts`: int
  - `graph_env`: `"prod"|"sandbox"`

3) `txnews_edge_memory`
- point_id：`edge:{src}|{relation}|{dst}`
- vector：`embedding(reason_text)`
- payload：
  - `edge_type`: `"edge"`
  - `src`: string（Node ID，如 `event:...`）
  - `dst`: string（Node ID，如 `ticker:...` 或 `event:...`）
  - `relation`: string（固定集合：`mentions|related_to|evolves_to|retrieval_priority|ignore`）
  - `weight`: float（0~1）
  - `confidence`: float（0~1）
  - `reason_text`: string（<= 800 chars）
  - `evidence_canonical_ids`: string[]（最多 64）
  - `updated_at_ts`: int
  - `graph_env`: `"prod"|"sandbox"`

### 1.2 Postgres 表（固定 4 个）

1) `feedback_logs`（已实现，若不存在则由 `init_db()` 自动建表）
- `uid`、`kind`、`data`、`created_at`

2) `kg_runs`
- `run_id`（uuid）、`graph_env`、`trigger_canonical_id`、`status`、`started_at`、`finished_at`

3) `kg_ops_log`
- `id`、`run_id`、`phase`（`planner|validator|critic|executor`）、`payload`（jsonb）、`created_at`

4) `kg_snapshots`
- `snapshot_id`、`run_id`、`graph_env`、`snapshot_payload`（jsonb：包含本次涉及的 Qdrant point ids 列表）、`created_at`

---

## 2. 任务与流水线（必须可跑通）

### 2.1 Celery 接入点（强制）

在现有链路 `normalize_raw -> dedup_store -> analyze -> deep_optimize` 之后，强制追加：
- `kg_update_from_canonical(canonical_id)`

### 2.2 任务：`kg_update_from_canonical`（规则版、幂等、分钟级）

输入：`canonical_id`

处理步骤（顺序固定）：
1) 从 Postgres 读取：
   - `articles`（title/text）
   - `analyses`（event_id/event_type/tickers/impact）
   - `article_versions`（最新 url/published_at）
2) 写 `txnews_entity_memory`（prod）：
   - 对每个 ticker：拼 `description_text = "{ts_code} {name}\\nindustry=...\\nrecent_event_id={event_id}\\nrecent_url={url}"`（截断）
   - embedding 后 upsert
3) 写 `txnews_event_memory`（prod）：
   - 取该 `event_id` 最近 12 篇 `canonical_id`（按分析时间倒序）
   - 规则生成 `snapshot_text`（固定模板：事件类型/关键 ticker 列表/最新 3 条标题/影响方向占位/证据 URL 列表）
   - embedding 后 upsert
4) 写 `txnews_edge_memory`（prod）：
   - `mentions`：`event:{event_id} -> ticker:{ts_code}`（weight=归一化频次；confidence=0.7）
   - `related_to`：对同窗口内其他 event 做 TopK（K=6）相似连接（基于 ticker overlap + 同 event_type bonus；confidence=0.55）
   - 每条边必须写 `reason_text` 与 `evidence_canonical_ids`
5) 记录审计：
   - 写 `kg_runs` / `kg_ops_log`（phase=executor）/ `kg_snapshots`

### 2.3 周期治理任务（必须实现）

1) `kg_gc`（每小时执行）
- 删除/降权规则（固定）：
  - `updated_at_ts < now - 7d` 的 `ignore` / `retrieval_priority` 控制边：从 Qdrant 删除
  - `updated_at_ts < now - 30d` 的 `related_to` 边：`weight *= 0.5`（重新 upsert）

2) `kg_reconcile`（每日执行）
- 对过去 24h 的 `event_id` 重新生成 `snapshot_text`，保证一致性与压缩质量（规则版）。

---

## 3. GraphOps（v2.2 安全带：计划-执行分离）

GraphOps 在本项目的固定实现：
- LLM 只允许输出 `GraphOpsPlan JSON`（planner）。
- Validator 进行 schema/约束/证据校验。
- Critic 输出 `EvalReport JSON`（KEEP/WEAKEN/REMOVE）。
- Executor 仅执行 Validator+Critic 通过的 ops；先写 sandbox，再 commit 到 prod。

实现要求（不可省略）：
- sandbox 与 prod 的隔离：使用 Qdrant payload 字段 `graph_env`（同 collection 分区），并在写入时强制带上。
- commit 策略：先写 Postgres 审计（`kg_runs/kg_ops_log/kg_snapshots`），再写 Qdrant；失败必须可重试且幂等。

---

## 4. API（对 UI/Agent 的固定契约）

### 4.1 Dashboard 3D 图谱

- `GET /kg/graph?minutes=180`
  - 返回 `{nodes, links, stats, generated_at}`
  - nodes 仅包含 `event` 与 `ticker`
  - links 至少包含 `mentions`

### 4.2 反馈闭环

- `POST /feedback`：body `{kind, data}`（由 cookie uid 归属用户）

---

## 5. 前端：Dashboard 实时 3D 知识图谱（必须交付）

固定实现：
- 页面：`/dashboard`（现有看板页面）
- 组件：`apps/web/src/components/KG3DGraph.vue`
- 渲染：Three.js（WebGL），深色现代风格（渐变背景 + 星点 + 低透明连线）
- 更新：每 3 秒轮询 `/kg/graph` 刷新图
- 交互：
  - 点击节点显示侧栏详情（事件：最近证据链接；实体：名称/ts_code）
  - 点击证据链接上报 `/feedback`（kind=`kg_graph_evidence_click`）
  - 节点赞/踩上报 `/feedback`（kind=`kg_graph_thumb_up|kg_graph_thumb_down`）

---

## 6. 验收（DoD：必须全部通过）

1) 数据：
- 新增一条新闻进入 pipeline 后，`txnews_event_memory` / `txnews_entity_memory` / `txnews_edge_memory` 均在分钟级出现更新（prod）。
2) 可解释：
- 任意一条 edge payload 的 `evidence_canonical_ids` 非空；`reason_text` 非空。
3) UI：
- `/dashboard` 可稳定渲染 3D 图谱；窗口切换（60m/180m/720m）可实时更新；点击节点可看到证据链接。
4) 反馈：
- 任意点击/赞踩会写入 `feedback_logs`（可用 SQL 验证最新记录存在）。
