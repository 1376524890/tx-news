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
| `V2_NEWS_KG_IMPLEMENTATION.md` | 实施权威 | v2.2 一次性全量交付的可执行步骤文档（数据结构/任务/API/UI/验收/回滚）。 |
| `V2_NEWS_KG_DESIGN.md` | 入口 | 指向 `V2_NEWS_KG_IMPLEMENTATION.md`（不再承载实现细节）。 |
| `V2_NEWS_KG_ATTACHMENT_DEV_GUIDE.md` | 入口 | 指向 `V2_NEWS_KG_IMPLEMENTATION.md`（不再承载实现细节）。 |
| `V2_NEWS_KG_DELIVERY_PLAN.md` | 入口 | 指向 `V2_NEWS_KG_IMPLEMENTATION.md`（不再承载实现细节）。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
