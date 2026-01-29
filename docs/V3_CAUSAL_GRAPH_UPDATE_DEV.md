<!-- Input: v3 causal roadmap + P0/P1/P2 gaps + current implementation -->
<!-- Output: v3 causal upgrade dev log and implementation guide -->
<!-- Pos: v3 causal update guide (update header and docs/FOLDER.md on change) -->

# v3 因果图谱更新开发文档（P0/P1/P2）

本文记录本轮因果图谱的升级内容、配置、实现位置与后续扩展建议，便于回溯与二次迭代。

## 1. 目标与范围

- **P0**：因果置信度可分解、Event→Entity 合成器显式化。
- **P1**：变量 scope 约束 + 冲突消解（反证降权）。
- **P2**：对外路径暴露与离线回放（本轮先完成埋点与结构，接口后续可拓展）。

## 2. 关键变化概览

### 2.1 置信度可分解 schema
- 所有 `causal` 边携带 `confidence_breakdown`：
  ```json
  {
    "support_events": 12,
    "same_direction_ratio": 0.83,
    "temporal_validity": 1.0,
    "recentness": 0.7,
    "conflict_ratio": 0.12,
    "path_support": 0.65,
    "event_var_confidence": 0.74
  }
  ```
- 变量统计边 `variable_impacts_entity` 同步包含 `confidence_breakdown`。

### 2.2 Event→Entity 合成器显式化
- 新增内部函数 `EventEntityCausalSynthesizer`（在 `causal.py` 中以函数形式实现）
- 只有当满足以下门槛，才折叠路径生成 `Event → Entity` 因果边：
  - `event_var_confidence >= event_var_min_conf`
  - `variable_impacts_entity.weight >= var_entity_min_weight`
  - `support_events >= min_support`

### 2.3 变量 scope 约束
- `config/causal_variables.yaml` 支持 `applies_to.sectors` 字段。
- 统计阶段会过滤不在 scope 内的实体，减少稀释噪声。

### 2.4 冲突消解（反证降权）
- 若同一变量对同一实体出现正/负方向同时强支持，触发冲突降权。
- 公式：
  ```text
  if conflict_ratio > threshold:
      confidence *= (1 - conflict_penalty * conflict_ratio)
  ```

## 3. 配置项

新增/更新配置（`config/config.yaml: causal`）：
- `event_var_min_conf`
- `var_entity_min_weight`
- `conflict_ratio_threshold`
- `conflict_penalty`
- 既有的 `lookback_days/max_events/min_support/...` 继续使用

变量白名单（`config/causal_variables.yaml`）：
- 支持 `applies_to.sectors`，例：
  ```yaml
  - name: Financing Cost
    domain: firm
    applies_to:
      sectors: [real_estate, manufacturing]
  ```

## 4. 代码落点（索引）

- 白名单 + 正规化：`src/tx_news/analysis/causal_vars.py`
- 分析层输出：`src/tx_news/tasks/pipeline.py` / `src/tx_news/tasks/deep_analysis.py`
- KG 变量候选边：`src/tx_news/tasks/kg.py`（`event_impacts_variable`）
- 统计 + 合成任务：`src/tx_news/tasks/causal.py`
- 任务注册：`src/tx_news/tasks/celery_app.py`

## 5. 输出结构（边 schema）

### 5.1 variable_impacts_entity
```json
{
  "relation": "variable_impacts_entity",
  "effect_direction": "+",
  "support": 8,
  "support_pos": 7,
  "support_neg": 1,
  "consistency": 0.875,
  "conflict_ratio": 0.125,
  "confidence": 0.54,
  "confidence_breakdown": {
    "support_events": 8,
    "same_direction_ratio": 0.875,
    "conflict_ratio": 0.125,
    "temporal_validity": 1.0,
    "recentness": 0.5
  }
}
```

### 5.2 causal
```json
{
  "relation": "causal",
  "direction": "+",
  "confidence": 0.78,
  "confidence_breakdown": {
    "support_events": 12,
    "same_direction_ratio": 0.83,
    "temporal_validity": 1.0,
    "recentness": 0.7,
    "conflict_ratio": 0.12,
    "path_support": 0.65,
    "event_var_confidence": 0.74
  },
  "via_variables": ["Financing Cost"]
}
```

## 6. 运行方式

- 因果合成在 `analysis_updated` / `deep_analysis_updated` 后自动触发。
- 若需要手动触发：
  ```bash
  python -m tx_news.tasks.causal.causal_synthesize_from_canonical <canonical_id>
  ```

## 7. 风险与注意事项

- **白名单缓存**：修改 `causal_variables.yaml` 后需重启 worker 才能生效。
- **行业映射粗糙**：scope 过滤通过关键词映射行业，后续可接入更稳定行业分类。
- **冲突降权**：默认惩罚系数较保守（0.6），可通过配置调整。

## 8. 后续迭代建议

- **P2：对外路径暴露**
  - 增加 API：`/kg/graph?include_causal=true`
  - Agent 工具新增：`get_event_causal_paths`
- **离线回放/反事实**
  - 取历史事件窗口，冻结旧图谱，比较真实价格反馈与预测方向
  - 建议新增 `tasks/causal_replay.py`

---

本文件用于记录 v3 因果图谱当前状态与演进路径，后续变更请同步更新。
