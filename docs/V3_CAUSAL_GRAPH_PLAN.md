<!-- Input: v2 KG + 分析流水线现状 + 因果分层方案（事件→变量→实体） -->
<!-- Output: v3 因果图谱升级方案（差距评估、结构设计、落地步骤与决策点） -->
<!-- Pos: v3 因果图谱设计与落地计划（变更时同步更新以上注释与 docs/FOLDER.md） -->

# v3 因果图谱升级方案（Plan + Checklist）

> 目标：在不推翻 v2 KG 的前提下，把“相关性边”升级为“有向因果边”，并确保可审计、可回滚、可解释。

## 1. 背景与现状简述

当前 v2 KG 具备 event/ticker 节点与 mentions/related_to 边，具备证据链、sandbox/prod、审计与回滚，但 **缺少变量层、缺少因果边合成阶段**，且 **置信度并非多源证据函数**。

## 2. v3 设计目标

- **因果边是“事件级可审计因果”**，禁止从 mentions/related_to 直接升级。
- **LLM 只做事件→变量**，不做最终因果权重与实体因果边。
- **置信度为可分解函数**：证据强度、一致性、时间对齐、路径支持。
- **保留 v2 基础能力**：规则边继续存在，UI/Agent 可逐步增量升级。

## 3. 核心分层结构（v3）

### Layer 0：Event → Entity（候选影响对象池）
- 继续保留 mentions，但语义标记为 correlation/candidate。

### Layer 1：Event → Variable（因果起点）
- LLM 仅输出变量白名单中的变量与方向。
- 新增变量节点类型 `latent_variable`。

### Layer 2：Variable → Entity（稳定统计）
- 不由 LLM 生成，来自历史事件统计。
- 作为因果路径的稳定“物理定律”。

### Layer 3：Causal Edge Synthesizer（合成因果边）
- 仅在多源证据累积后，产出最终 `relation=causal` 边。

## 4. 数据结构与 Schema 变更

### 4.1 新增变量白名单
- 文件：`config/causal_variables.yaml`（或合并到 `config/config.yaml`）
- 示例：
  ```yaml
  variables:
    - name: Interest Rate
      domain: macro
      directions: ["+", "-"]
      desc: "Policy rate / market rates"
      applies_to:
        sectors: [banking, real_estate]
  ```

### 4.2 新增 variable 节点
- `node_type = latent_variable`
- 典型 payload：
  ```json
  {
    "node_type": "latent_variable",
    "var": "Financing Cost",
    "domain": "macro",
    "description_text": "Financing Cost (macro)"
  }
  ```
> 存储方案已选：复用 `txnews_entity_memory`（通过 `node_type` 区分）。

### 4.3 新增边类型
- `event_impacts_variable`（候选）
- `variable_impacts_entity`（统计）
- `causal`（最终因果边）

## 5. 分析层改动（LLM 只负责 Event→Variable）

### 5.1 新增输出字段
`analysis.data` 中新增：
```json
"affected_variables": [
  {"var":"Financing Cost","direction":"+","confidence":0.72,"evidence":["cid1","cid2"]}
]
```

### 5.2 Prompt 约束
- 强制从白名单变量中选择。
- 必须输出 direction；不得生成实体因果边。

### 5.3 结果校验
- 新增 validator，删除白名单外变量；保留原始输出到 `_txnews.llm_raw`。
- scope 约束：统计阶段按 `applies_to.sectors` 过滤实体。

## 6. KG 构建改动

### 6.1 Planner 生成新边
- `event_impacts_variable`：候选边，confidence 低、证据必须完整。
- `variable_impacts_entity`：统计边（事件回溯驱动）。
- mentions/related_to 继续保留，标记为 correlation。

### 6.2 Causal Edge Synthesizer（新增任务）
- 聚合最近 N 次同类事件，计算最终因果边。
- 置信度合成：
  ```text
  final_conf = 0.35*evidence + 0.25*consistency + 0.20*temporal + 0.20*path_support
  ```
- 只有 `final_conf > threshold` 才写入 `relation=causal`。
- 输出包含 `confidence_breakdown`（support/consistency/temporal/recentness/conflict 等）。

## 7. API / Agent / UI 改动

- `/kg/graph` 支持 `include_causal=true` 返回变量节点与 causal edges。
- Agent 工具新增：
  - `get_event_causal_paths(event_id)`
  - `explain_causal_edge(src, dst)`
- Dashboard 可先隐藏变量节点，仅展示因果路径解释面板。

## 8. v3 落地步骤（推荐顺序）

1) **Schema 与白名单**：添加变量白名单与 node/edge 类型定义。
2) **分析层输出**：加入 `affected_variables` 并做白名单校验。
3) **KG 候选边写入**：Event→Variable 候选边入图。
4) **Synthesizer 上线**：合成因果边并写入 KG。
5) **API/Tools/UI 增量显示**：开放 causal paths 结果。

## 9. 兼容性与回滚

- v2 mentions/related_to 边不改语义，只加标记字段。
- 变量节点与因果边可全部写入 sandbox，验证通过再提交 prod。
- Synthesizer 产出的因果边可通过 `kg_rollback` 回滚。

## 10. 风险与缓解

- **LLM 变量幻觉** → 白名单过滤 + 原始输出保记。
- **变量→实体映射错误** → 统计阈值 + 低置信度衰减。
- **因果边过多** → threshold + decay + gc。

## 11. 已选方案（已确认）

### 11.1 变量节点存储方式
**B. 复用 `txnews_entity_memory`**
- 优点：改动小、上线快。
- 注意：检索需按 `node_type` 过滤变量节点。

### 11.2 变量→实体边的来源
**B. 统计驱动（历史事件回溯）**
- 优点：可自更新，适配市场变化。
- 注意：需要设定窗口/阈值，避免噪声累积；冲突方向会触发降权。

### 11.3 Causal Synthesizer 触发方式
**B. 事件驱动（新事件触发）**
- 优点：更实时。
- 注意：需加锁与最小支持阈值控制频繁更新。

## 12. 实施检查清单（代码指令）

- 新增配置：`config/causal_variables.yaml`
- GraphOps 扩展：新增变量节点/边类型
- Analysis 输出：`affected_variables` 字段
- 深分析输出同步：deep_analysis 也产出变量层
- KG Planner：新增 event→variable 边
- 统计逻辑：variable→entity 边生成逻辑
- 新任务：`tx_news/tasks/causal.py`（Synthesizer）
- API/Agent/UI：支持因果路径查询
- 文档：同步更新 `docs/FOLDER.md` 与 `src/tx_news/kg/FOLDER.md`

---

如需继续扩展（例如引入规则表/外部价格数据），请在此方案基础上补充。
