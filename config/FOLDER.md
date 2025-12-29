<!-- Input: config.yaml 与 sources.txt 的配置内容 -->
<!-- Output: 供 tx_news.settings 读取的本地配置文件 -->
<!-- Pos: 配置目录索引（变更时同步更新以上注释与本文件内容） -->

# `config/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 以文件形式管理 v0 单机配置：crawler/retention/embedding/llm/tushare 等。
- `sources.txt` 提供抓取入口 URL 列表（可注释/空行）。
- 配置由 `src/tx_news/settings.py` 加载并注入各进程（embedding 支持 `device/use_fp16/cache_dir` 等本地推理参数；Tushare 主数据支持缓存 TTL）。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `config.yaml` | 主配置 | 抓取并发/保留策略/embedding/LLM/Tushare/事件窗口等。 |
| `sources.txt` | 数据源列表 | 抓取入口 URL（每行一个；支持 `#` 注释）。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
