<!-- Input: 设计/提案类 Markdown 文档 -->
<!-- Output: docs/ 目录架构概览与文件清单（含关键变更备注） -->
<!-- Pos: docs/ 索引文档（变更时同步更新以上注释与本文件内容） -->

# `docs/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 本目录用于沉淀“版本设计/架构提案/路线图”等内容；不直接承诺已落地实现。
- 设计文档应明确：目标/非目标、与现有实现的映射点、增量落地里程碑与回滚策略。
- 涉及数据结构变更时，需同步标注：Postgres/Qdrant/MinIO 的落地点与兼容策略。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `V2_NEWS_KG_IMPLEMENTATION.md` | 技术路线 | v2 技术路线总结（架构、数据流、调用链、完整程序框图、图谱自我更新、数据落点与约束）。 |
| `V2_NEWS_KG_DESIGN.md` | 入口 | 指向 `V2_NEWS_KG_IMPLEMENTATION.md`（入口指向，不展开细节）。 |
| `V2_NEWS_KG_ATTACHMENT_DEV_GUIDE.md` | 入口 | 指向 `V2_NEWS_KG_IMPLEMENTATION.md`（入口指向，不展开细节）。 |
| `V2_NEWS_KG_DELIVERY_PLAN.md` | 入口 | 指向 `V2_NEWS_KG_IMPLEMENTATION.md`（入口指向，不展开细节）。 |
| `ANALYSIS_GRAPH_PIPELINE.md` | 流程文档 | 分析图谱网络从入库→分析→KG 更新→看板聚合的搭建与迭代流程。 |
| `V3_CAUSAL_GRAPH_PLAN.md` | 方案文档 | v3 因果图谱升级方案（差距评估、结构设计、落地步骤与决策点）。 |
| `V3_CAUSAL_GRAPH_UPDATE_DEV.md` | 开发文档 | v3 因果图谱更新开发记录（P0/P1/P2 落地与配置说明）。 |
| `V3_CAUSAL_GRAPH_WORKFLOW.md` | 流程图 | v3 因果图谱流程/数据流/逻辑图与 KG 运行机制。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
