<!-- Input: 单元测试用例与测试夹具 -->
<!-- Output: 本仓库测试目录的职责索引 -->
<!-- Pos: 测试目录索引（变更时同步更新以上注释与本文件内容） -->

# `tests/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 以 `pytest` 为主的单元测试目录，避免真实外网/真实基础设施依赖。
- 通过 `monkeypatch`/fakes 覆盖 NATS/Postgres/MinIO 等外部交互点。
- 关注关键流程的“不中断”与错误处理行为。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `test_crawler_collector.py` | 单元测试 | 覆盖采集器在网络/中间件异常场景下的降级与不中断行为。 |
| `test_kg_rules.py` | 单元测试 | 覆盖 v2 KG 规则模块（ID/快照文本/边打分）的确定性与截断约束。 |
| `test_news_queue_deep_trigger.py` | 单元测试 | 覆盖队列工人触发分析时的 `is_new_canonical` 标记，避免深分析被跳过。 |
| `test_scripts_check_crawler_connectivity.py` | 回归测试 | 覆盖 `check_crawler_connectivity.sh` 的 dry-run 行为（不触网/不依赖 Docker）。 |
| `test_scripts_start_stop_local.py` | 回归测试 | 覆盖 `start_local.sh`/`stop_local.sh` 的 --help 行为（不触网/不依赖 Docker）。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
