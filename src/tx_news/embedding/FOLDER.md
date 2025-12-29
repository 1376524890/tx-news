<!-- Input: 文本与 embedding 模型名/路径 -->
<!-- Output: 归一化向量（list[float]） -->
<!-- Pos: 向量化层索引（变更时同步更新以上注释与本文件内容） -->

# `src/tx_news/embedding/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 通过 `sentence-transformers` 在 GPU（若可用）/CPU 上计算向量，支持 `device`/`use_fp16`/`cache_dir` 配置。
- 支持模型 id 简写与本地目录路径（可离线运行；避免强依赖网络下载）。
- 向量用于语义去重与向量检索（Qdrant）。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包标识 | 标记 embedding 子包。 |
| `embedder.py` | 向量化实现 | 模型加载、名称规范化与 `embed()`。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
