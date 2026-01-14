<!-- Input: 现有 TX-News 数据流（collector→worker→Postgres/Qdrant）与 Chat 工具增强对话需求 -->
<!-- Output: v2 自连接自迭代新闻知识图谱（语义连续知识库）系统设计与里程碑 -->
<!-- Pos: 版本更新设计文档（变更时同步更新以上注释与所属目录 FOLDER.md） -->

# TX-News v2：自连接自迭代新闻知识图谱（语义连续知识库）设计

本文是“设计稿”，目标是在现有 TX-News v1 的采集/去重/分析/向量检索基础上，构建一个**高时效、可自连接、可自迭代**的新闻知识图谱系统，用于：
1) 发掘新闻事件间的关联与影响路径；2) 面向 Chat 查询提供“证据驱动”的连续知识反馈。

## 0. 现状基线（v1 已有能力）

- **数据流**：`apps/collector` → NATS → `apps/worker`(Celery) → Postgres/Qdrant/MinIO。
- **向量库**：Qdrant 存 canonical 文章向量（`txnews_articles`，维度随 embedding 模型自动兼容 collection）。
- **事件归并**：`tasks/pipeline.analyze()` 通过 `event_type + key_entity + time_window` 生成稳定 `event_id`，可用 `/events/{event_id}` 拉时间线。
- **Chat**：Agent 强制“先工具检索再回答”，当前工具以 `list_recent` + `search_news` 为主（证据来自 Postgres/Qdrant）。

结论：v1 已有“事件中心 RAG”的雏形，但缺少**事件↔事件的长期关联结构**与**随新证据迭代的连续知识载体**。

## 1. 目标与非目标

### 1.1 目标（必须）

- **高时效**：新新闻进入系统后，分钟级可在 Chat 检索与回答中体现（Fresh-First）。
- **自连接**：自动发现并维护 `event↔event`、`event↔entity` 的语义关联，支持“为什么相关/可能的传导链”。
- **自迭代**：随着新证据到来，事件摘要/关系权重可自动更新；支持合并/拆分、漂移修正与过期衰减。
- **连续知识表达**：核心知识以“连续语义空间（embedding + 叙事快照）”表达，避免仅靠离散表结构做拼接。

### 1.2 非目标（v2 不做或延后）

- 不追求严格形式化本体（ontology）或完整因果证明；v2 以“可解释的语义关联 + 证据”优先。
- 不强依赖在线 LLM；LLM 不可用时仍能用规则/向量方法给出可用关联（质量降级但不断流）。
- 不在 v2 直接做复杂图算法（如全图社区发现/图神经网络训练）；先做可增量维护的轻量策略。

### 1.3 Graph RAG 核心思想（适配本项目）

- **节点（Node）**：可被检索、可被引用、可被生成的知识单元（本质是“文本载体 + embedding + 元数据”）。
- **边（Edge）**：控制检索路径、上下文拼接、时序演化、因果/逻辑约束的结构信息（本质是“可解释的连接 + 权重/置信度 + 证据”）。

本项目的约束与取舍：
- **连续知识优先**：主知识形态落在 Qdrant（向量 + payload），而不是依赖大量离散表 join；Postgres 仅作为审计/回滚/可视化的最小索引。
- **时效优先**：边与检索排序必须显式引入时间衰减与“近时校准”（Fresh-First），避免向量近邻把旧相似内容拉上来。
- **可解释优先**：任何关系边都必须能回溯到 `canonical_id/url` 证据集合；边的“原因文本”本身也可向量化以辅助解释检索。

## 2. Graph RAG Schema：把“图”做成可迭代的语义记忆

### 2.1 节点（Nodes）：本项目需要的知识单元

v2 的节点遵循统一思想：每个节点都要有“可引用的文本载体”，让它既能被检索召回、也能在回答中作为证据/摘要被引用。

#### (1) Document / Chunk（文章/片段）节点：`ArticleNode`

- **对应现状**：canonical 文章已存在（Postgres `articles` + Qdrant `txnews_articles`）。
- **典型字段（payload）**：
  - `node_type: "article" | "chunk"`（v2 初期可先只做 article）
  - `canonical_id`（稳定 id）
  - `text`（不建议在 Qdrant 存全文；可存 title + short abstract）
  - `embedding`（已存在）
  - `source_id/url/published_at/fetched_at`
  - `quality_score`（可选：去重置信、媒体权重等）
- **作用**：向量检索入口 + 证据链接落点。

#### (2) Entity（实体）节点：`EntityNode`

- **对应现状**：已有 ticker 匹配 + `/entities/{ts_code}` 主数据；但还缺“可检索的实体记忆文本”。
- **实体类型建议**：
  - `ticker/company`（A 股 ts_code 为主键）
  - `org`（机构/监管部门/协会）
  - `concept/industry/macro`（行业链、宏观概念、政策名）
- **典型字段**：
  - `node_type: "entity"`
  - `entity_id`（`ts_code` 或 `sha256(entity_type|name)`）
  - `entity_type`、`name`、`aliases`
  - `description_text`（短：公司简介/行业/别名/关键属性/近期事件引用）
  - `embedding`
- **作用**：减少“语义漂移”（query→entity→event 的桥接），并作为多跳扩展的锚点。

#### (3) Event（事件）节点：`EventNode`（事件级可迭代记忆）

- **对应现状**：已有 `event_id`（由 `event_type + key_entity + time_window` 生成）。
- v2 增强为“事件记忆快照（Summary/State）序列”，每次更新都会产出一个可检索的短文本载体：
  - `snapshot_text`：发生了什么 + 关键实体 + 可能传导路径 + 不确定性 + 证据 URL 列表（压缩到 1～2KB）
  - `snapshot_embedding`：对 `snapshot_text` 的 embedding（用于 event-level 检索与 event↔event 连接）
  - `snapshot_meta`：`updated_at`、覆盖窗口、纳入的 canonical_ids、来源分布、`last_published_at`
- **保留策略**：只保留最近 N 个快照（例如 N=24），体现“连续演化”且便于衰减/归档。

#### (4) Fact / Claim（事实/断言）节点：`ClaimNode`（v2.3 起但可提前留口）

- **来源**：
  - 规则版：从 `analyses.data` 中结构化字段模板化生成（例如“X 公司发布业绩预告，影响方向为 …”）。
  - LLM 增强：从多篇证据中抽取“可审计断言”（必须附 evidence）。
- **典型字段**：
  - `node_type: "claim"`
  - `claim_id`（`sha256(subject|predicate|object|event_id|window_start)`）
  - `subject/predicate/object`（或 `claim_text`）
  - `confidence`
  - `evidence_canonical_ids`
- **作用**：把“图的推理边”落到可审计证据链上（supports/refutes/causes）。

#### (5) Query / Task（查询/任务）节点：`QueryNode`（不一定持久化）

- **定位**：每次 Chat 请求的临时节点（可选做短期缓存），包含 `query_text/intent/constraints`。
- **作用**：驱动“动态子图构建”（先找实体→找事件→找证据）。

#### (6) State / Memory（状态/记忆）节点：`StateNode`

- **定位**：事件记忆本质就是 StateNode 的一种；也可扩展为“市场状态/舆情状态/政策预期状态”。
- **作用**：支持自迭代闭环（新证据到来→状态更新→边权调整→检索路径改变）。

### 2.2 边（Edges）：本项目需要的连接类型

边决定“能不能走、走多远、用什么拼上下文、如何保证时效/可信”。建议先做可增量维护、成本可控的边类型：

#### (1) 语义关系边（semantic）

- `article → entity: mentions`（由 ticker/实体识别得到）
- `event_snapshot → event_snapshot: related_to`（由 event_memory 向量近邻得到）
- `entity → entity: related_to`（行业/概念/同一事件共现）

字段：`relation/weight`（`weight` 可来自 cosine similarity + 实体重叠 bonus）。

#### (2) 结构关系边（structural / hierarchy）

- `chunk → article: part_of`
- `event_snapshot → event: summarizes`
- `claim → event: belongs_to`

字段：`relation`（通常不需要 weight）。

#### (3) 逻辑/推理边（reasoning）

- `claim → claim: supports | refutes`
- `event → event: causes | implies`（只在有足够证据与不确定性约束下生成）

字段：`relation/confidence/evidence`（必须带证据 canonical_ids；可选附“原因文本”）。

#### (4) 时间/演化边（temporal）

- `event_snapshot(t) → event_snapshot(t+1): evolves_to`
- `event_a → event_b: before | after`（基于 `published_at` 与窗口）

字段：`timestamp`（或 `source_time/target_time`）。

#### (5) 检索与控制边（control，LLM-aware）

用于把“时效性/可信度/降噪”显式注入检索路径（这是新闻场景的关键）：

- `event_snapshot → *: retrieval_priority`（近时/权威来源/高置信边权提升）
- `* → *: ignore`（低质量源、重复转载、旧事件在新窗口内降权）

字段：`weight`（可随时间衰减）、`reason`（为什么 boost/ignore）。

### 2.3 极简 Schema（通用、可落地）

节点（Node）最小字段：
- `id`
- `node_type`
- `text`（或 `name/description_text`）
- `embedding`（或 `embedding_ref`）
- `metadata`（`timestamp/source/confidence/quality_score/...`）

边（Edge）最小字段：
- `source_id`
- `target_id`
- `edge_type`
- `relation`
- `weight`（可选）
- `confidence`（可选）
- `evidence_ids`（建议：`canonical_id` 列表）

## 3. 存储与索引（连续知识优先）

### 3.1 Qdrant：主检索与“连续语义空间”

建议以“节点可检索”为第一原则组织 collection；边可先走轻量实现（Postgres 表或 Qdrant edge_memory 二选一）。

建议新增/拆分 collection（与现有 embedding 维度兼容策略保持一致）：

- `txnews_articles`：文章向量（已存在）。
- `txnews_event_memory`：事件快照/摘要向量（Summary/State 节点；payload 含 `event_id`、`snapshot_id`、`updated_at`、`canonical_ids`、`last_published_at`）。
- `txnews_entity_memory`：实体记忆向量（Entity 节点；payload 含 `entity_id/entity_type/name/aliases`）。
- `txnews_claims`（可选）：断言向量（Claim 节点；payload 含 `claim_id/subject/predicate/object/confidence/evidence_ids`）。
- `txnews_edge_memory`（可选）：把“边”也做成可检索对象（EdgeNode：relation explanation text + embedding + `source_id/target_id/weight/evidence_ids`），用于“解释为什么相关”。

### 3.2 Postgres：最小化的“可回滚与可解释”元数据（可选但推荐）

为避免“图结构完全埋在 payload”导致难以治理，建议在 Postgres 存 **最小索引**：

- `kg_events`：`event_id`、`latest_snapshot_id`、`updated_at`、统计字段（articles_count、last_published_at）。
- `kg_event_snapshots`：`snapshot_id`、`event_id`、`snapshot_text`、`updated_at`、`embedding_ref`（指向 Qdrant point）。
- `kg_edges`：`event_a`、`event_b`、`relation_type`、`weight`、`updated_at`、`evidence_canonical_ids`（少量即可）。

注意：Chat 的主召回仍以 Qdrant 为主；Postgres 表仅用于审计、可视化与一致性维护。

## 4. 在线更新（自连接/自迭代）流水线

### 4.1 触发点：沿用现有 Celery 任务链

在 v1 的 `dedup_store → analyze → deep_optimize` 后新增一个任务：

- `kg_update_from_canonical(canonical_id)`：增量更新 Graph RAG 子图（节点 + 边），面向“分钟级可用”的时效要求。

建议该任务一次性完成（不强依赖 LLM）：
- **节点**：upsert `EntityNode`（实体记忆）、`EventNode snapshot`（事件快照），可选 upsert `ClaimNode`（断言）。
- **边**：upsert `mentions/related_to/evolves_to/retrieval_priority` 等边（或等价写入 edge_memory/edges 表）。
- **控制**：对边权/检索排序注入 `recency_decay` 与 `quality_score`（权威源/重复转载降噪）。

### 4.2 节点更新（Entity/Event/Claim）

输入：`canonical_id` 对应的（title、analysis、published_at、tickers、event_id）。

#### 4.2.1 实体记忆更新（EntityNode）

- 以 `analyses.data.entities/tickers` 为主来源（v1 已产生），把“实体”变成可检索的短文本记忆：
  - 公司：`ts_code + name + industry + aliases + 近期事件引用（event_id 列表）`
  - 机构/概念：`name + aliases + 近期高频共现实体/事件`
- embedding 后 upsert 到 `txnews_entity_memory`（不存原文，只存简介/链接）。

#### 4.2.2 事件快照更新（EventNode Snapshot，自迭代）

过程（优先无 LLM 可运行；LLM 仅做增强润色/纠错）：

1) 取该 `event_id` 的最近 M 篇文章（可通过 analyses.data.event_id + 时间排序）。
2) 生成 `snapshot_text`（建议 1～2KB 内）：
   - 规则版：拼装“事件类型 + 关键实体 + 近 M 篇标题摘要 + 影响方向占位 + 风险/不确定性模板 + 证据 URL”。
   - LLM 增强版（可选）：在规则版基础上进行压缩与纠错，产出更稳定的叙事。
3) 对 `snapshot_text` 做 embedding，upsert 到 `txnews_event_memory`。
4) 在 payload 中写入：`canonical_ids`（证据集合）、`last_published_at`、`updated_at`、`event_type/key_entity`，并更新 `kg_events.latest_snapshot_id`（或 payload 内 `latest=true` 标记）。

输出：可被 `search_events` 工具直接检索的“事件级证据面板”。

#### 4.2.3 断言节点更新（ClaimNode，可选）

- v2 初期可先不落地 ClaimNode；但建议在 schema 中预留：
  - 规则版：把 `impact/index_view/event_type` 模板化为 1～3 条短断言（必须带证据 canonical_ids）。
  - LLM 增强：从事件快照 + 多篇证据中抽取“可审计断言”（supports/refutes/causes），仍需 evidence。

### 4.3 边更新（semantic / temporal / control / reasoning）

对每次新快照，构建并维护“可增量更新”的边集合：

- **semantic（相关性）**：
  - `event_snapshot → event_snapshot: related_to`：在 `txnews_event_memory` 里检索 TopK（如 K=20），过滤同 event_id。
  - `article → entity: mentions`、`event → entity: mentions`：来自 v1 识别结果（tickers/entities）。
  - `entity → event: related_to`：entity 近期被哪些 event 覆盖（可用倒排索引或简单统计）。
- **temporal（演化）**：`event_snapshot(t) → event_snapshot(t+1): evolves_to`（按更新时间/证据集合变更）。
- **control（时效/降噪）**：对任意可扩展边写入 `retrieval_priority/ignore`：
  - 近时 boost：`recency_decay = exp(-Δt/τ)`（τ 可按 event_type 配置）。
  - 权威 boost：媒体源权重/重复转载降权（可从 source_id 做白/灰名单）。
- **reasoning（推理，可选）**：仅在 ClaimNode 存在时启用 `supports/refutes/causes`，并强制 evidence。

对候选边计算权重（示例）：

`weight = sim * recency_decay * entity_overlap_bonus`

其中 `recency_decay = exp(-Δt / τ)`（τ 可按 event_type 或全局设置），确保**时效性**：旧事件不会长期主导检索。

将边写入：
- Qdrant（`txnews_edge_memory`，便于“关系解释检索”）；以及/或者
- Postgres（`kg_edges`，便于可视化与治理）。

### 4.4 合并/拆分与漂移修正（自迭代的关键，v2.2 起）

提供周期性维护任务（例如每 10 分钟/每小时）：

- `kg_reconcile_events()`：
  - 合并：两个 event_id 的快照长期高度相似 + 实体高度重叠 → 生成 merge 记录与 alias。
  - 拆分：同一 event_id 内快照语义出现明显双峰/漂移 → 拆分为 event_id2（保留历史映射）。

合并/拆分不追求完美，但必须满足：可回滚、可解释（给出证据 canonical_ids）。

## 5. 查询与 Chat 反馈：从“文章检索”升级为“事件记忆 + 关系路径”

### 5.1 工具层新增（Agent Tools）

新增工具（示例）：

- `search_events(q, limit, recent_hours)`：检索事件快照（而非直接检索文章），返回 event_id、摘要、关键实体、证据 URL。
- `search_entities(q, limit)`：检索实体记忆（entity_memory），用于 query→entity→event 的桥接与去漂移。
- `get_event_neighbors(event_id, limit)`：返回与该事件最相关的邻居事件及关系解释（优先带证据）。
- `get_entity_events(entity_id, limit)`：从实体桥接到相关事件（“公司/行业最近发生了什么”）。
- `explain_connection(event_a, event_b)`：若存在 edge_memory/edges，输出可解释的连接理由与证据集合。

### 5.2 Fresh-First（保证时效性）

Agent（Graph RAG）策略固定为（Query Node → 子图 → 证据 → 生成）：

1) 先 `list_recent(minutes=...)` 获取近时证据（无论用户问什么都做一次“新鲜度校准”）。
2) 为 query 建立临时 `QueryNode`（可不持久化），并执行：
   - `search_entities(q)`（先锁定实体，减少语义漂移）
   - `search_events(q)`（召回事件快照）
3) 多跳扩展（最多 1～2 跳，避免图幻觉）：
   - `get_event_neighbors`（沿 semantic/control 边扩展）
   - `get_entity_events`（沿 mentions/related_to 边扩展）
4) 拉取证据文章（canonical_id/url/published_at）并压缩为上下文（不含原文长段）。
5) LLM 生成：输出结论 + 影响路径 + 不确定性 + 证据链接（citation）。

> 这能避免“向量库召回到旧相似内容”，从流程上确保“新”优先。

### 5.3 输出原则（合规与可解释）

- 不输出新闻原文/长段引用（沿用现有约束）。
- 每个结论都给出可点击 URL 证据列表（来自 versions/evidence）。
- 对关系边必须带“不确定性”与“可能机制”说明（避免伪确定的因果）。

## 6. 里程碑（版本更新设计）

### v2.0（最小可用：事件记忆）

- 新增 `txnews_event_memory` + `txnews_entity_memory` 两类连续知识库（Qdrant collections）。
- 新增 `search_events` + `search_entities` 工具（Query→Entity→Event 的基础路径）。
- 事件快照规则版生成（不依赖 LLM），能在 Chat 中检索并引用证据。
- 加入 `recency_decay` 重新排序，保证近时优先。

### v2.1（事件自连接：关系边）

- 新增 `txnews_edge_memory`（或 Postgres `kg_edges`）与 `get_event_neighbors/explain_connection` 工具。
- 基于事件快照相似度 + 实体重叠生成边；输出“为什么相关”的解释文本与证据。
- 引入 control 边（`retrieval_priority/ignore`）与边权衰减，强化新闻时效与降噪。

### v2.2（自迭代治理：合并/拆分/衰减）

- 周期任务：合并/拆分、边权衰减、旧快照压缩/归档。
- UI/API：提供 event graph 可视化接口（供前端或 admin 使用）。

### v2.3（更细粒度：Claim/Contradiction，可选）

- 从分析结果中抽取“断言/事实片段（claim）”作为节点，支持冲突检测与版本对照。
- 提供“同一事件不同媒体口径”对比能力。

## 7. 与现有代码的映射（落地提示）

建议新增模块（仅为建议，不在本设计稿中强制）：

- `src/tx_news/kg/`：事件记忆、边生成、合并拆分、时间衰减策略。
- `src/tx_news/tasks/kg.py`：Celery 任务封装（`kg_update_from_canonical`、`kg_reconcile_events`）。
- `src/tx_news/agent/tools.py`：新增 KG 工具方法与返回结构（保持“不返回原文”）。
- `apps/api/main.py`：可选新增 `/kg/*` 内部接口（供调试/可视化）。

## 8. 风险与控制

- **召回漂移**：embedding 模型变化会导致 event_memory 断层 → 复用现有 Qdrant collection 兼容策略（scoped/auto）。
- **成本/延迟**：LLM 仅作为增强；规则版快照必须可用，且可在 CPU 模式运行。
- **错误关联**：边必须带证据与不确定性；默认只扩展 1～2 跳，避免“图幻觉”。
- **合规**：事件快照与关系解释只存短文本摘要 + URL，不存原文长段。
