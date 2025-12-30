<!-- Input: 本项目 deep analyse 调用链 + prompt I/O + schema 约束 -->
<!-- Output: 面向 LLaMA-Factory 的数据集规划与生成规范（领域/格式/方向/数量） -->
<!-- Pos: txdatasets 数据集生成指南（变更时同步更新以上注释与所属目录 FOLDER.md） -->

# TX-News Deep Analyse 微调数据集规划（txdatasets）

本目录用于指导生成“针对性微调”数据集，把 `src/tx_news/tasks/deep_analysis.py:deep_optimize()` 的 LLM 调用替换为本地微调模型（通常通过 vLLM 提供 OpenAI-compatible `/v1/chat/completions`）。

本项目中 LLM 的“深分析（deep analyse）”不是闲聊总结，而是一个 **结构化回写任务**：在已有初步分析结果的基础上，结合“相似新闻证据包”和“事件窗口（minutes）”进行二次推理，输出严格 JSON，写回 `analyses` 表供 API/UI 使用。

---

## 1) Deep Analyse 的实际流程（以代码为准）

Deep Path 触发点：
- 上游任务：`src/tx_news/tasks/pipeline.py:analyze()`（规则分析 + 可选 LLM JSON 增强）会先写入一份 `analysis.data`。
- Deep Path：仅当 `canonical.is_new_canonical == True` 且配置了 LLM API key 时，触发 `deep_optimize.delay(canonical)`。

`deep_optimize()` 的关键步骤（决定了训练样本的输入结构）：
1) 用目标新闻正文 embedding 做 Qdrant 相似检索（TopN=8），构造 `evidence`（仅元信息：canonical_id/score/title/url/published_at）。
2) 从 Postgres 读取当前 `analysis.data`（作为“初步分析”输入）。
3) 根据初步 `event_type` 选择事件窗口 `minutes`（来自 `config/config.yaml:event_windows_minutes`）。
4) 以 system+user prompt 要求模型只输出一个 JSON 对象（`response_format=json_object`），字段集合固定：
   - `event_type, entities, tickers, impact, index_view, evidence`
5) 将模型输出与原 `analysis.data` merge 后 upsert 回 DB，并发出 `deep_analysis_updated` signal。

因此：**微调数据集的输入必须包含 “目标新闻 + 初步分析 + 相似证据包 + minutes”，输出必须是严格 JSON 且字段可被系统直接消费。**

---

## 2) I/O 规范（必须与线上调用兼容）

### 2.1 输出 JSON（强约束）

输出必须满足：
- 仅输出 1 个 JSON 对象（不允许解释、markdown、前后缀文本）。
- 至少包含以下 key（可扩展但不建议引入与 UI/评测无关的新 key）：
  - `event_type`（str）
  - `entities`（list）
  - `tickers`（list）
  - `impact`（dict）
  - `index_view`（dict）
  - `evidence`（list）
- 证据与实体 **不得凭空编造**（尤其是 URL、ts_code）。

`event_type`（当前项目规则集，见 `src/tx_news/analysis/rules.py`）：
- `policy`
- `macro_data`
- `company_event`
- `geopolitics`
- `industry_supply_demand`
- `liquidity`
- `other`（证据不足/无法归类时兜底）

系统兼容性注意（与数据集规划直接相关）：
- `deep_optimize()` 会更新 DB 中 `Analysis.event_type` 列，但当前实现不会重算/覆盖 `analysis.data.event_id`；如果你希望“纠正 event_type 后 event_id 也一致”，需要先改造代码并在训练/推理时提供可重算 event_id 的必要输入（window_start、key entity 等）。
- `minutes` 由“初步分析的 event_type”决定；因此训练样本里应把 `minutes` 视为已给定条件，而不是让模型自行重算窗口长度。

事件窗口（当前默认配置，见 `config/config.yaml`）：
- `policy=1440`，`macro_data=1440`，`liquidity=1440`
- `company_event=2880`
- `geopolitics=4320`，`industry_supply_demand=4320`

推荐（便于稳定输出与评测）的字段形状：
- `entities`: `[{ "type": str, "name": str, "ts_code": str?, "role": str?, "confidence": float? }]`
- `tickers`: `[{ "ts_code": str, "name": str, "confidence": float? }]`
- `impact`: `{ "scope": "index"|"industry"|"stock", "direction": "up"|"down"|"uncertain", "confidence": 0~1, "horizon": "intraday"|"days"|"weeks"? }`
- `index_view`: `{ "direction": "up"|"down"|"uncertain", "drivers": string[], "risks": string[] }`
- `evidence`: `[{ "url": str, "published_at": str?, "source_id": str?, "note": str? }]`

强烈建议加入 “不确定” 样本：当输入证据包不足、相似新闻冲突、或目标新闻本身为传闻/二手来源时，输出应落在 `direction="uncertain"` 且 `confidence` 偏低，并在 `risks` 写清不确定来源。

### 2.2 输入格式（训练时建议最大化贴近线上 prompt）

线上 `deep_optimize()` 的 user prompt 结构（可作为数据生成模板的“字段清单”）：
- `目标新闻标题`：str
- `目标新闻正文`：线上为原文截断（<=5000 chars）
- `初步分析`：来自 DB 的 `analysis.data`（dict 或 None）
- `相似新闻证据（TopN，含时间与链接）`：list[dict]（最多 8 条）
- `minutes`：事件窗口分钟数（由初步 event_type 决定）

数据合规建议（与仓库 README 保持一致）：训练集 **不写入新闻原文**。为了尽量贴近线上 prompt，同时避免原文泄露，建议：
- `目标新闻正文` 用“改写后的短摘要/要点”替代（3~8 条 bullet 或 200~600 字连续文本均可）。
- `相似新闻证据` 只保留元信息（title/url/published_at），不填原文摘录。

---

## 3) 微调目标与数据方向（要覆盖什么能力）

deep analyse 微调要解决的核心问题是：**把初步分析纠偏到“可回写、可聚合、可解释（drivers/risks）且严格 JSON”**。因此数据方向建议按以下维度组织：

### 3.1 纠错与补全（必选）
- `event_type` 纠错：初步分析错分/过粗/混淆时纠正（例如 macro_data vs liquidity；company_event vs industry_supply_demand）。
- `tickers` 纠错：补全缺失 ticker、剔除误匹配 ticker（仅保留输入候选中合理的部分；不要新造 ts_code）。
- `entities` 补全：补全政策主体、指标名称、商品品种、产业链环节、关键公司等实体，并标注 role（driver/risk/subject）。
- `impact/index_view` 稳定化：输出方向一致、链路清晰（drivers 与 risks 可落地），并能在证据不足时输出不确定。

### 3.2 证据包使用（必选）
- 证据选择：从相似新闻列表中挑选 0~3 条写入输出 `evidence`，并在 `note` 中说明“为何相关”。
- 冲突处理：证据互相矛盾/时间超窗/相关性弱时，输出应显式降低置信度并写入 risks。
- 去幻觉约束：输出中使用的 URL 必须来自输入（目标新闻 URL 或证据 URL）。

### 3.3 事件窗口意识（建议）
输入包含 `minutes` 的本质用途是“事件归并/时间线维护”。数据中应覆盖：
- 同一事件窗口内的“同主题不同稿”（加强归并一致性）。
- 跨窗口的“余波/跟进报道”（输出倾向更保守，或在 evidence 中标注时间差）。

---

## 4) 数据集范围（领域覆盖）

领域定位：A 股相关的中文新闻（宏观/政策/行业/公司/外部冲击），目标是生成可被系统消费的结构化分析 JSON。

建议覆盖的主题（映射到 event_type）：
- `policy`：监管/政策文件、会议表态、行业准入、地方政策、交易制度调整。
- `macro_data`：CPI/PPI/PMI/GDP/社融/M2 等数据与预期差（同比/环比/预期/分项结构）。
- `liquidity`：利率走廊、公开市场操作、资金面松紧、信用派生与杠杆。
- `industry_supply_demand`：产能/限产/复产、库存/价格、供需错配，产业链传导（上游—中游—下游）。
- `company_event`：财报/预告、订单与交付、回购/增减持、并购重组、诉讼、退市/破产风险。
- `geopolitics`：制裁、关税、出口管制、冲突升级/缓和，对产业链与风险偏好影响。
- `other`：无法可靠归类或信源不清晰（强调不确定与风险提示）。

实体类型建议（用于 `entities`）：
- `ticker`（股票实体，带 `ts_code`）
- `org/company`（上市公司/机构）
- `policy`（政策/监管动作）
- `macro_indicator`（宏观指标）
- `commodity`（原油/煤/铜/硅料…）
- `industry`（光伏/半导体/券商/地产…）
- `country/region`（美国/欧盟/中东…）
- `index`（沪深300/创业板指等；如未维护代码，可仅写 name）

---

## 5) 数量规划（分阶段，含配比）

下面给出一个“可落地”的 LoRA/QLoRA SFT 规模规划（以 7B~14B 基座、`cutoff_len≈4096` 为参考）。如果你只本地化 deep analyse，优先做 `DeepOptimize-SFT`；`Analyze-SFT` 仅在你计划进一步本地化 `pipeline.analyze()` 时补齐。

### 5.1 DeepOptimize-SFT（主数据集，推荐）

最小可用（MVP）：
- 总量：`1,400 ~ 2,100` 条
- 配比：每个 `event_type` 约 `200 ~ 300` 条（`other` 额外 `200` 条用于“不确定/证据不足”）
- 目标：先把“严格 JSON + 字段稳定 + 去幻觉”打稳

可上线迭代（v1）：
- 总量：`8,000 ~ 12,000` 条
- event_type 分布建议：
  - policy / macro_data / liquidity：各 `1,200 ~ 1,800`
  - company_event：`1,200 ~ 1,800`
  - industry_supply_demand / geopolitics：各 `800 ~ 1,200`
  - other：`800 ~ 1,200`
- 场景配比建议：
  - 初步分析基本正确、深分析“补全证据与链路”：`60%`
  - 初步分析明显错误、深分析“纠错”：`30%`
  - 证据冲突/不足（输出不确定）：`10%`

高强度版本（v2，可选）：
- 总量：`30,000+`（以自动化 silver 生成 + 过滤为主，人工只抽检与修正）
- 目标：覆盖更多细分行业与长尾事件，提升鲁棒性与一致性

### 5.2 Analyze-SFT（可选数据集）

仅当你计划本地化 `pipeline.analyze()` 时建议准备：
- 总量：`2,000 ~ 5,000` 条
- 重点：从“标题+摘要要点+候选 tickers”直接输出基础 JSON（字段同上），并保留一定比例的不确定样本。

---

## 6) 数据生成流程（建议的可执行步骤）

### 6.1 从系统中抽取“可用元信息”（不含原文）

建议用于构造样本的字段来源：
- 目标新闻：`canonical_id`、`title`、`published_at`、`url/source_id`（来自 versions）
- 初步分析：`analysis.data`（deep 前的版本，或在同一 event_id 下的历史版本）
- 相似证据包：用当前 embedding + Qdrant 召回得到的 topN 元信息（title/url/published_at/score/canonical_id）
- 事件窗口 minutes：按初步 event_type 与 `event_windows_minutes` 取值

注意：如果你需要从正文生成摘要要点，请把“摘要生成过程”和“原文存储”与训练集物理隔离，确保最终写入 `*.jsonl` 的只有改写/脱敏后的要点。

### 6.2 标注（gold/silver）策略

建议采用分层策略：
- Gold（少量高质量）：人工标注/复核输出 JSON（用于评测集 + 核心训练集种子）
- Silver（大量自动化）：用当前云端 LLM 生成 deep analyse 输出，再用规则/脚本过滤（JSON 可解析、字段齐全、URL 不越界、tickers 不乱造），并抽样人工回查

---

## 7) 质量门槛（强烈建议做自动校验）

写入 `txnews_deep_analysis_sft_alpaca.jsonl` 前建议至少满足：
- `output` 可被 `json.loads` 解析为 dict
- `output` 仅包含 1 个 JSON 对象（无前后文本）
- `event_type` 在允许集合内（或统一映射到 `other`）
- `evidence[*].url` 必须来自输入的 URL 集合
- `tickers[*].ts_code` 不新增（应来自输入候选，或来自你维护的 name→ts_code 映射）

一条简单的本地校验命令（示例）：
```bash
python - <<'PY'
import json
from pathlib import Path
p = Path("finetune/txdatasets/txnews_deep_analysis_sft_alpaca.jsonl")
bad = 0
for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
    obj = json.loads(line)
    try:
        out = json.loads(obj["output"])
        assert isinstance(out, dict)
        for k in ["event_type","entities","tickers","impact","index_view","evidence"]:
            assert k in out
    except Exception:
        bad += 1
print("lines:", i, "bad:", bad)
PY
```

---

## 8) 本目录文件说明

- `txnews_deep_analysis_sft_alpaca.jsonl`：DeepOptimize-SFT 主数据集（Alpaca JSONL）
- `dataset_info.json`：LLaMA-Factory dataset registry（`dataset_dir` 指向本目录时可直接引用）
- `FOLDER.md`：目录索引（新增/调整文件时同步更新）

当前仓库内置样本量（便于对齐训练配置与抽样评测）：
- `txnews_deep_analysis_sft_alpaca.jsonl`：500 条
