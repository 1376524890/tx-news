<!-- Input: v3 causal implementation + pipeline/KG/agent docs -->
<!-- Output: v3 causal workflows with diagrams (process/dataflow/logic) -->
<!-- Pos: v3 causal workflow doc (update header and docs/FOLDER.md on change) -->

# v3 因果图谱：流程/数据流/逻辑图与 KG 运行机制

本文件汇总 v3 因果图谱的完整运行方式，包含：程序流程图、数据流图、逻辑图，以及 KG（知识网络）运行机制细节。

> 说明：所有图示使用 Mermaid（可在 GitHub/支持 Mermaid 的 Markdown 渲染器中查看）。

---

## 1. 程序流程图（End-to-End）

```mermaid
flowchart TD
  A[Collector 拉取]
  B[Normalize/Readability]
  C[Dedup + Canonical]
  D[Analyze: LLM/Rules]
  E[analyses 写库]
  F[KG 更新: kg_update_from_canonical]
  G[因果合成: causal_synthesize_from_canonical]
  H[Deep Analysis: optional]
  E2[analyses 更新]
  I[Qdrant: event/entity/edge memory]
  J[Agent/Tools 查询]
  K[Dashboard /kg/graph]

  A --> B --> C --> D --> E
  E --> F
  E --> G
  D --> H --> E2
  E2 --> F
  E2 --> G
  F --> I
  G --> I
  I --> J
  I --> K
```

---

## 2. 数据流图（表/库/集合）

```mermaid
flowchart LR
  subgraph DB[Postgres]
    A1[articles]
    A2[article_versions]
    A3[analyses]
    A4[signals]
    A5[kg_runs/kg_ops_log/kg_snapshots]
  end

  subgraph VDB[Qdrant]
    Q1[txnews_event_memory]
    Q2[txnews_entity_memory]
    Q3[txnews_edge_memory]
  end

  subgraph Cache[Redis]
    R1[analysis_queue]
    R2[locks]
  end

  A1 --> A3
  A2 --> A3
  A3 --> Q1
  A3 --> Q2
  A3 --> Q3
  A3 --> A4
  A3 --> A5
  Q1 --> Q3
  Q2 --> Q3
  R1 --> A3
  R2 --> A3
```

---

## 3. 逻辑图（事件→变量→实体）

```mermaid
flowchart TD
  E[Event]
  V[Latent Variable]
  T[Entity/Ticker]

  E -->|mentions| T
  E -->|event_impacts_variable| V
  V -->|variable_impacts_entity| T
  E -->|causal| T
```

说明：
- `mentions/related_to` 保留为相关性边（correlation）。
- `event_impacts_variable` 来自 LLM 输出的变量白名单（候选）。
- `variable_impacts_entity` 来自统计回溯（稳定）。
- `causal` 由 EventEntityCausalSynthesizer 合成（显式门槛）。

---

## 4. KG（知识网络）运行机制

### 4.1 运行阶段

```mermaid
flowchart TD
  S[Trigger: analysis_updated/deep_analysis_updated]
  P[Planner]
  V[Validator]
  X[Executor: sandbox]
  C[Critic]
  Y[Commit to prod]
  Z[Stop/Log]
  Snap[Snapshot Before]
  End[Done]

  S --> P --> V --> X --> C
  C -->|pass| Y --> Snap --> End
  C -->|fail| Z
```

### 4.2 Planner 负责什么
- 生成可执行 GraphOpsPlan（节点/边）
- 确保边具备证据 `evidence_canonical_ids`
- v3 新增：
  - 变量节点 `latent_variable`
  - `event_impacts_variable` 候选边
  - `variable_impacts_entity` 统计边
  - `causal` 因果边

### 4.3 Validator/Executor/Critic
- Validator：保证 payload 完整性（节点文本、边证据、reason_text）
- Executor：向 Qdrant 写入（sandbox → prod）
- Critic：当前为确定性 pass，可扩展为规则/质量门槛

### 4.4 回滚机制
- 每次 prod 写入前存 `kg_snapshots`
- 可通过 `kg_rollback(snapshot_id)` 回滚

---

## 5. 因果合成（EventEntityCausalSynthesizer）

```mermaid
flowchart TD
  E[Event] --> EV[Event→Variable candidates]
  EV --> VE[Variable→Entity stats]
  VE --> C[Event→Entity Synthesizer]
  C -->|threshold| CE[causal edge]
```

### 5.1 合成条件（门槛）
- `event_var_confidence >= event_var_min_conf`
- `variable_impacts_entity.weight >= var_entity_min_weight`
- `support_events >= min_support`

### 5.2 置信度模型（显式分解）
示例：
```json
{
  "confidence": 0.78,
  "confidence_breakdown": {
    "support_events": 12,
    "same_direction_ratio": 0.83,
    "temporal_validity": 1.0,
    "recentness": 0.7,
    "conflict_ratio": 0.12,
    "path_support": 0.65,
    "event_var_confidence": 0.74
  }
}
```

---

## 6. 知识网络（KG）运行方式细节

### 6.1 触发
- `analysis_updated` / `deep_analysis_updated` → 异步触发
- 每次触发：
  - `kg_update_from_canonical` 维护 v2 KG
  - `causal_synthesize_from_canonical` 生成 v3 因果边

### 6.2 并发与一致性
- Redis 分布式锁避免重复计算
- 规则幂等：event_id + ticker + relation 唯一
- 先写 sandbox，再批量提交 prod

### 6.3 GraphOps 结构
- Node: `event`, `ticker`, `latent_variable`
- Edge: `mentions`, `related_to`, `event_impacts_variable`, `variable_impacts_entity`, `causal`
- 所有边必须携带 `evidence_canonical_ids`

---

## 7. Agent 工具链（知识网络对话使用）

```mermaid
flowchart LR
  Q[User Query] --> A[TxNewsAgent]
  A --> T1[search_entities/search_events]
  A --> T2[get_event_neighbors]
  A --> T3[get_event_causal_paths]
  T3 --> R[Event→Variable→Entity Paths]
  A --> R2[Explain + Evidence]
```

---

## 8. 后续可扩展模块

- `/kg/graph?include_causal=true`（展示因果边）
- `causal_replay` 离线回放任务（预测 vs 真实反馈）
- 更精细的行业/主题 scope 映射

---

如需继续扩展接口与 UI 层，我可以直接补全 API + 前端展示方案。
