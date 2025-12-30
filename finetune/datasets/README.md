<!-- Input: 深分析任务定义 + 结构化输出 schema -->
<!-- Output: LLaMA-Factory 数据集使用与扩充指南 -->
<!-- Pos: 深分析微调数据集说明（变更时同步更新以上注释与所属目录 FOLDER.md） -->

# TX-News 深度分析（Deep Analysis）微调数据集

目标：把“深度分析”从云端 API 模型切换到本地微调模型（通过 vLLM 提供 OpenAI 兼容接口），用于 `src/tx_news/tasks/deep_analysis.py` 的二次推理与结构化回写。

约束（必须遵守）：
- **不训练/不输出新闻原文**：输入只允许短摘要（自行改写/脱敏后的要点），输出只允许结构化结论。
- 输出 **必须是单个 JSON 对象**，无多余文本（方便 `response_format=json_object` 强约束）。
- 输出字段对齐系统现有 schema：`event_type, entities, tickers, impact, index_view, evidence`。

## 数据集文件

- `txnews_deep_analysis_sft_alpaca.jsonl`
  - 每行 1 条样本，字段：`instruction` / `input` / `output`
  - `instruction` 固定为“只输出 JSON 对象”的系统约束
  - `input` 为结构化 JSON（标题/摘要/初步分析/证据）
  - `output` 为结构化 JSON（修正后的 event_type/影响路径/证据）

## 输出字段建议（可扩充但不应破坏兼容）

- `event_type`: `policy|macro|company|commodity|credit|fx|rates|geopolitics|industry|other`（示例，最终以项目规则为准）
- `entities`: `[{type,name,ts_code?,role?,confidence?}]`
- `tickers`: `[{ts_code,name,confidence?}]`
- `impact`: `{scope: "index"|"stock"|"industry", direction: "up"|"down"|"uncertain", confidence: 0~1, horizon?: "days"|"weeks" }`
- `index_view`: `{direction, drivers: string[], risks: string[]}`
- `evidence`: `[{url, source_id?, published_at?, note?}]`

## 如何扩充数据（推荐）

1) 从生产库抽取 **结构化元信息**（标题、链接、时间、已有分析 JSON、相似新闻列表），不要抽取正文。
2) 人工/规则生成“摘要要点”（3~8 条 bullet），保证不包含长段原文。
3) 用“你期望深分析模型输出的最终 JSON”作为 `output`。
4) 保持多样性：政策/财报/产业链/商品/宏观/地缘等；也要包含“不确定/证据不足”的样本。

