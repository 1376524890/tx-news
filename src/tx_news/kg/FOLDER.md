<!-- Input: v1 分析结果（event_id/event_type/tickers）+ Qdrant 连续知识库 -->
<!-- Output: v2 知识图谱规则生成、ID 规范与 GraphOps schema -->
<!-- Pos: v2 KG 逻辑层索引（变更时同步更新以上注释与本文件内容） -->

# `src/tx_news/kg/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 提供 v2 KG 的纯逻辑模块：ID 规范、规则版 event snapshot、相关度打分、GraphOps 数据结构与校验。
- 不直接做 IO（Qdrant/Postgres 读写在 tasks/storage 层实现），便于单测与复用。

## 文件
| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `ids.py` | 规范 | Node/Edge ID 与 Qdrant point_id 生成规则（含 sandbox/prod 隔离）。 |
| `snapshot.py` | 规则 | 事件快照文本（snapshot_text）规则生成与截断。 |
| `scoring.py` | 规则 | related_to 等边的权重/原因文本生成（ticker overlap + event_type bonus）。 |
| `graphops.py` | 规范 | GraphOpsPlan/EvalReport schema 与 Validator 规则（最小可执行集合）。 |
| `__init__.py` | 包入口 | 导出 kg 公共函数。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |

