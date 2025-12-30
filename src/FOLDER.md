<!-- Input: Python 源码 -->
<!-- Output: 可安装的 Python 包 -->
<!-- Pos: 源码根目录 -->

# `src/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 遵循 `src-layout` 布局，包含核心业务逻辑包 `tx_news`。
- 隔离源代码与项目根目录配置，避免导入歧义。

## 文件
| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `tx_news/` | 核心包 | 包含所有核心业务逻辑（Crawler, ETL, Storage, Agent）。 |
| `tx_news.egg-info/` | 元数据 | pip install -e . 生成的包信息（gitignored）。 |
