<!-- Input: NATS 连接串与要发布的 payload -->
<!-- Output: JetStream stream 初始化与消息发布 -->
<!-- Pos: 事件总线封装索引（变更时同步更新以上注释与本文件内容） -->

# `src/tx_news/bus/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 统一封装 NATS JetStream 的建流与发布操作。
- Collector 使用它发布 raw 消息；Worker 侧用原生客户端消费；实现侧对 `nats-py` 做延迟导入以便单测环境可导入。
- 目标是让业务代码不直接散落连接/序列化逻辑。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包标识 | 标记 bus 子包。 |
| `nats.py` | 总线实现 | `ensure_stream()` 与 `publish()`。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
