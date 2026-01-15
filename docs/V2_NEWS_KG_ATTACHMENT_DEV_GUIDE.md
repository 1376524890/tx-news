<!-- Input: `docs/V2_NEWS_KG_DESIGN.md` 的系统设计与现有 TX-News v1 代码结构 -->
<!-- Output: 面向开发者的叙事型附件：重构目标、实现路径与技术路线说明 -->
<!-- Pos: 设计文档附件（变更时同步更新以上注释与所属目录 FOLDER.md） -->

# 附件：面向开发者的重构说明（Graph RAG 图生命周期系统）

交付入口：
- 交付技术路径与迭代拆分：`docs/V2_NEWS_KG_DELIVERY_PLAN.md`

这份附件不是“概念综述”，而是帮助你快速把握：我们要把 TX-News v1 改造成什么、为什么这么改、以及建议怎么落地。

如果你只记住一句话：**我们不是“画一张知识图”，而是做一套“图的生命周期系统”**——节点会随着新证据不断被改写、合并、弃用；边是可检验的推理假设，会被验证、削弱、移除，甚至被重连成更短的证据链。

## 1. 为什么要重构：新闻场景的两个痛点

TX-News v1 已经具备：
- 文章 canonical 去重、入库、embedding、Qdrant 检索
- `event_id`（事件窗口归并）与时间线 API
- 面向 Chat 的“先检索证据再回答”的工具循环

但在“高时效新闻”里，单纯的向量检索会遇到两件事：
1) **语义漂移**：用户问的是“某个公司/概念/事件的最新进展”，向量近邻却可能把旧相似内容排到前面。
2) **关系缺失**：事件之间的连接（影响链路、上下游、政策传导）不会“自己长出来”，只能靠 LLM 临场想象，难以复用与校验。

所以 v2 的核心改造是：把“可复用的知识”从“文章级 embedding”提升到“事件/实体的可迭代记忆”，并让“关系”成为可治理资产。

## 2. 目标形态：图不是静态结构，而是可演化的系统

### 2.1 Node（节点）是什么：可检索、可引用、可生成

在本项目里，节点不是数据库里的“几列字段”，而是一个**短文本载体 + embedding + 元数据**：
- 你可以检索它（Qdrant）
- 你可以引用它（回答时作为证据/摘要）
- 你可以改写它（随着新证据增量迭代，生成新版本）

我们主要关心三类“长期可用”的节点：
- `EntityNode`：实体记忆（公司/机构/概念）
- `EventSnapshotNode`：事件快照（事件在某一时刻的压缩叙事）
- `EdgeNode`（可选）：把“关系解释文本”也向量化，方便“为什么相关”的检索与展示

### 2.2 Edge（边）是什么：可检验的假设（Hypothesis）

边不是“永久真理”，更像“在当前证据集下，成立概率较高的连接假设”。

因此边必须具备：
- `evidence_ids`：能回溯到 `canonical_id/url` 的证据集合
- `confidence/status/last_validated`：可以被削弱、移除，并定期复核
- `rewire` 能力：当更合理的中间节点/路径出现时，允许把 `A→B` 重连为 `A→D→B`

## 3. 技术路线：尽量复用现有栈，减少引入新依赖

这里的选择原则是：**先把闭环跑起来，再谈更重的框架**。

### 3.1 存储：Qdrant 做“连续知识”，Postgres 做“可回滚元数据”

- Qdrant：存“可检索”的节点向量（event/entity/edge/claim 等）
- Postgres：存节点/边的版本、状态、证据索引、快照与回滚点（方便治理）

这符合你强调的“连续的非结构化知识属性”：主检索与主知识形态落在 embedding 空间，而不是靠复杂的表 join 拼上下文。

### 3.2 编排：沿用 Celery 任务流，把“图演化”接在 pipeline 后

v1 的链路是：
`dedup_store → analyze → deep_optimize（可选）`

v2 增加：
`→ kg_update_from_canonical（图增量更新） →（周期）kg_gc / kg_reconcile_events`

这样做的好处：
- 能保证分钟级上线（新闻一进来就触发图更新）
- 出问题可降级（图更新失败不影响文章入库与基础检索）

### 3.3 LLM：图构建/迭代默认用本地 vLLM，CPU 模式降级在线 LLM

这是你明确要求的策略：
- **GPU 模式**：图相关代理（Planner/Critic/Rewire/Rewrite）走 `llm.deep`（本地 vLLM，OpenAI 兼容接口）
- **CPU 模式**：自动回退到在线 LLM（走 `llm.chat` 或 `resolve_llm_deep()` 的 CPU 回退逻辑）

为什么要这么做：
- 图演化是高频任务（每条新闻都可能触发），用本地 vLLM 才能成本可控、时延稳定
- CPU 模式下本地推理成本太高/不可用，所以必须有“不断流”的在线降级

配置上建议开发者只需要记住：
- `TXNEWS_ACCELERATOR=gpu` → 配好 `llm.deep.base_url` 指向本地 vLLM
- `TXNEWS_ACCELERATOR=cpu` → 系统自动用在线 LLM（`llm.chat`）

## 4. 实现方法：GraphOps（计划-执行分离）是系统安全带

你会看到我们反复强调一句话：**LLM 不直接写数据库**。

实现上，LLM 只能输出结构化的 `GraphOps JSON`（例如 `UPSERT_NODE/ADD_EDGE/WEAKEN_EDGE/REWIRE`），然后由执行层：
1) 校验 schema（字段齐全、类型正确、relation 合法）
2) 校验约束（max_degree/max_hops、reasoning 边无环、证据存在）
3) 写入 sandbox，跑 critic/eval
4) 通过后 commit 到 prod，并打 snapshot

这套机制解决两类风险：
- LLM“胡乱改图”导致图结构崩坏
- 图演化误伤线上检索（sandbox/prod 隔离，随时回滚）

## 5. 开发者落地建议：从 v2.0 做到可用，再逐步增强

### v2.0：先把“事件/实体记忆”跑起来（最小可用）

你会实现四件事：
1) Qdrant 新增 `txnews_event_memory`、`txnews_entity_memory`
2) 任务：`kg_update_from_canonical` 生成事件快照 + 实体记忆（规则版即可）
3) 工具：`search_events`、`search_entities`（让 Query→Entity→Event 成为主路径）
4) 排序：强制 Fresh-First（`list_recent` 校准 + event_snapshot 的 `recency_decay`）

这一步就能显著改善：
- “问最新进展”不再总召回旧相似文
- 事件之间能开始形成可复用的连接入口（先 semantic 近邻即可）

### v2.1：把“边当作资产”引入验证与解释（自连接）

你会新增：
- `txnews_edge_memory` 或 `kg_edges`
- `get_event_neighbors/explain_connection`
- control 边（`retrieval_priority/ignore`）与边权衰减

此时用户问“为什么有关联”，系统不再靠临场编，而是：
1) 从 edge_store 找候选边
2) 返回“原因文本 + 证据列表”
3) 置信不足时允许 critic 把边削弱/移除

### v2.2：加入“治理任务”（合并/拆分/GC/漂移修正）

这一步的关键是把图从“能用”变成“能长期用”：
- 节点过期：低使用 + 旧时间窗 + 被覆盖 → 进入 deprecated/archived
- 边重连：置信下降或出现更短路径 → rewire
- 图快照：每次批量变更可回滚

## 6. 如何验证改造是有效的（不需要复杂基准）

建议开发者用“开发期可观测性”验证：
- 事件快照是否按新闻进入持续更新（`updated_at/last_published_at`）
- 查询是否先命中实体/事件，再回到文章证据（证据 URL 是否齐全）
- edge 变更是否可审计（谁创建、何时验证、为何削弱/移除、证据有哪些）
- CPU 模式是否自动走在线 LLM，GPU 模式是否优先走本地 vLLM（日志/配置检查）

## 7. 技术取舍总结（为什么这条路线最适合本项目）

- **不强上 Neo4j/LlamaIndex/LangGraph**：先用现有 Celery + Qdrant + Postgres 把“可用闭环”做出来，风险最低。
- **把“连续知识”放在 Qdrant**：符合新闻语义与非结构化连续属性，避免过度关系型建模导致迟滞。
- **用 GraphOps + sandbox/prod**：把“可演化”变成工程可控，而不是把系统稳定性交给 prompt。
- **本地 vLLM 优先，CPU 在线降级**：成本、时延、可用性三者兼顾，满足你的硬约束。
