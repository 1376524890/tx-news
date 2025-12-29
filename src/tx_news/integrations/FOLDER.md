<!-- Input: 外部数据源凭据（Tushare token 等）与网络/缓存 -->
<!-- Output: 规范化后的主数据行列表与本地缓存文件 -->
<!-- Pos: 外部集成索引（变更时同步更新以上注释与本文件内容） -->

# `src/tx_news/integrations/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 聚合外部数据源访问（Tushare/AkShare）并做本地缓存。
- 主数据写入由 `tasks/maintenance.py` 负责；这里专注“拉取+缓存”。
- 缓存路径稳定在 `var/cache/a_share/stock_basic.json`（默认 12h TTL，过期再更新）。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包标识 | 标记 integrations 子包。 |
| `tushare_sync.py` | 集成实现 | Tushare/AkShare 拉取与缓存读写。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
