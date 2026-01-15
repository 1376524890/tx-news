<!-- Input: 可复用 UI 逻辑 -->
<!-- Output: Vue 组件 -->
<!-- Pos: 前端通用组件目录 -->

# `apps/web/src/components/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 存放跨页面复用的 UI 组件。
- 目前主要业务逻辑内聚在 Views 中，通用组件按需拆分。

## 文件
| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `HelloWorld.vue` | 示例 | Vite 模板自带组件（可清理）。 |
| `KG3DGraph.vue` | 组件 | Three.js 实时 3D 知识图谱可视化（dashboard 用）。 |
| `TimeSeriesChart.vue` | 图表 | 轻量折线图组件（看板时序指标）。 |
