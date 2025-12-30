<!-- Input: 面向 LLaMA-Factory 的训练数据文件 -->
<!-- Output: 数据集目录索引与格式约定 -->
<!-- Pos: finetune 数据集目录（变更时同步更新以上注释与本文件内容） -->

# `finetune/txdatasets/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 提供可直接用于 LLaMA-Factory 的 SFT 数据集与 `dataset_info.json`。
- 数据集默认聚焦“deep analyse / 深分析”任务：输入为（改写后的）要点摘要 + 初步分析 + 相似证据包，输出为严格 JSON（不含新闻原文）。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `README.md` | 使用说明 | 训练目标、字段规范、如何扩充数据。 |
| `dataset_info.json` | LLaMA-Factory 配置 | 让 `dataset_dir` + `dataset` 能定位到本目录数据文件。 |
| `txnews_deep_analysis_sft_alpaca.jsonl` | SFT 数据集 | Alpaca 格式（instruction/input/output）。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
