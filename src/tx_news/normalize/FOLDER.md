<!-- Input: HTML bytes 与网页元信息 -->
<!-- Output: 规范化的 title/text/checksum/published_at 等字段 -->
<!-- Pos: 清洗/规范化层索引（变更时同步更新以上注释与本文件内容） -->

# `src/tx_news/normalize/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- `readability.py` 抽取正文与标题，并计算文本 checksum。
- `metadata.py` 从 meta/time 标签解析发布时间（尽力而为）。
- 该层输出供 pipeline 后续去重、入库与分析使用。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包标识 | 标记 normalize 子包。 |
| `readability.py` | 正文抽取 | Readability + BeautifulSoup 生成纯文本。 |
| `metadata.py` | 时间解析 | 从 HTML 提取发布时间。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
