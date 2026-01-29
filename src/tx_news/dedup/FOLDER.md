<!-- Input: 新闻正文文本与 canonical_id -->
<!-- Output: 近重复候选 canonical_id 与 LSH 索引持久化文件 -->
<!-- Pos: 去重层索引（变更时同步更新以上注释与本文件内容） -->

# `src/tx_news/dedup/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 使用 MinHash + LSH 做近重复检索（适用于转载/轻改写），并记录时间戳用于窗口裁剪；插入时支持幂等更新以避免重复 key 抛错。
- 索引持久化到 `var/lsh_index.pkl`（本地文件），按 `TXNEWS_DEDUP_WINDOW_HOURS` 只保留近窗口数据。
- pipeline 在语义去重前先做近重复快速判定（同样限定窗口内）。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包标识 | 标记 dedup 子包。 |
| `lsh.py` | 去重实现 | tokenize/minhash + LSH load/save + 查询/写入。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
