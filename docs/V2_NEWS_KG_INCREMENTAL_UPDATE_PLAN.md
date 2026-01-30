<!-- Input: 现有 KG 更新链路 + 队列/性能瓶颈 + Qdrant/Postgres 现状 -->
<!-- Output: KG 增量更新方案（阶段化落地路径 + 数据结构变更 + 风险/回滚） -->
<!-- Pos: KG 增量更新设计方案（变更时同步更新以上注释与 docs/FOLDER.md） -->

# KG 增量更新方案（Phase 2 设计）

> 目标：把 KG 从“事件驱动全量写入”演进到“实体驱动的增量更新”，降低运行成本并减少重复写入。

## 1. 背景与问题

- 当前 `kg_update_from_canonical` 会对 event → ticker → edge 做全量构建与写入。
- 一旦分析结果里 tickers 发散（如 LLM 输出数百个），KG 更新就会变成 O(N) 写入 + O(N) embedding。
- sandbox/prod 双写导致 embedding 与 Qdrant upsert 成本翻倍。
- 因为 KG 更新耗时过长，阻塞默认队列，影响上游 ingest/analyze。

## 2. 目标 / 非目标

目标：
- **降低重复更新**：避免同一 event 重复刷新同一批节点/边。
- **减少 embedding 计算**：只对“语义变化”的节点重新向量化。
- **解耦“状态更新”与“向量更新”**：payload 更新不触发 embedding。
- **可回滚**：逐阶段落地、可关闭。

非目标：
- 不在本阶段重构 KG schema。
- 不引入新的存储服务。
- 不改变事件归并逻辑（event_id 仍由 analyze 提供）。

## 3. 现状约束

- 节点/边都以 `node_id_*`、`edge_id` 作为 key 写入 Qdrant。
- Qdrant 封装目前只有 `upsert/search/retrieve/scroll/delete`，缺少 `set_payload`。
- `kg_update_from_canonical` 内部是“plan → validate → sandbox → prod”双执行模式。

## 4. 分阶段设计

### 4.1 最小版（挡 80% 重复）

**核心策略：同 event 重跑直接跳过。**

判定规则（任选其一）：
- 方式 A（Postgres）：新增 `kg_event_state` 表，记录 `event_id` 的最后更新时间/版本号。
- 方式 B（Qdrant）：查询 event 节点是否存在（`event_id` + `graph_env=prod`）。

推荐：
- **优先方式 A**，理由：不用依赖 Qdrant 存在/一致性，并可记录更多状态。

**数据结构（建议）**
```sql
create table if not exists kg_event_state (
  event_id text primary key,
  last_canonical_id text,
  last_analyzed_at timestamptz,
  last_snapshot_hash text,
  updated_at timestamptz default now()
);
```

**运行逻辑（伪码）**
```
event_id = analysis.event_id
snapshot_hash = sha256(snapshot_text + tickers + variables)
state = load_state(event_id)
if state and state.last_snapshot_hash == snapshot_hash:
    return {"skipped": True, "reason": "event_unchanged"}
else:
    run_kg_update()
    upsert_state(event_id, snapshot_hash, canonical_id, analyzed_at)
```

**落地成本：低**
- Postgres 增加表 + 查询/写入（在 kg_update_from_canonical 中新增 10~20 行）。

### 4.2 正确版（实体/关系级增量）

**核心策略：节点/边只在“语义变化”时重写向量，payload 更新走 set_payload。**

关键变化：
1. **ticker 节点：**
   - embedding_text = `"{ts_code} {name} {industry}"`（稳定）
   - payload 更新：`recent_event_id/recent_url/last_seen_ts` 通过 `set_payload` 更新
   - 若 embedding_text 未变化，则只更新 payload，不再 embedding

2. **event 节点：**
   - snapshot_text 变化才 re-embed，否则只更新 `last_published_at_ts` 等 payload

3. **edge 节点：**
   - edge_key 已唯一（`edge_id(src, relation, dst)`），如果 evidence/weight 未变化，只更新权重
   - 仅当 reason_text 变化时才重新 embedding

**需要补齐的基础能力**
- 在 `QdrantStore` 增加：
  - `set_payload(point_id, payload)` 或 `update_payload(point_id, payload)`
  - `retrieve(point_id, with_vectors)` 已有，可用于判断是否存在

**运行逻辑（伪码）**
```
node = qdrant.retrieve(node_id)
if node exists:
    if node.payload.description_text == embedding_text:
        qdrant.set_payload(node_id, diff_payload)
    else:
        qdrant.upsert(node_id, embed(embedding_text), full_payload)
else:
    qdrant.upsert(...)
```

**注意**
- embedding_text 的稳定性是前提（已在 pipeline/ kg 层稳定化）。
- payload 变更不应影响 embedding。

### 4.3 边的幂等键策略（关系级）

- 当前 edge_key 已经是唯一键：`edge_id(src, relation, dst)`。
- 增量策略：只更新 evidence、weight、confidence、updated_at_ts；无必要则跳过写入。
- 可考虑为 evidence list 计算 hash，只在变化时写入。

## 5. 指标与验证

建议新增指标：
- kg_update_duration_seconds（P95）
- kg_ops_count_by_type（UPSERT_TICKER/UPSERT_EVENT/UPSERT_EDGE）
- kg_skip_reason（event_unchanged / node_unchanged / edge_unchanged）
- embedding_calls_total

## 6. 风险与回滚

风险：
- 误判“无变化”导致漏更新（影响图谱 freshness）
- payload 与向量不同步（需要保证稳定文本逻辑正确）

回滚策略：
- 保留 feature flag，例如 `TXNEWS_KG_INCREMENTAL=0/1`
- 若出现异常，切回全量写入模式

## 7. 落地建议顺序

1. **4.1 事件级跳过**（一周内可落地，立即降重复）
2. **payload 更新能力**（QdrantStore 增加 set_payload）
3. **4.2 实体级增量**（先 ticker，再 event，再 edge）
4. **加指标 + 观测**（可与 2/3 同步）

---

本方案可与现有 KG 框架兼容，且不改变既有 schema；建议先从事件级增量切入，确保收益快速可见。
