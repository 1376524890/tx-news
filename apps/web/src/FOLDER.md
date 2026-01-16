<!-- Input: Vue 组件与逻辑 -->
<!-- Output: 编译后的 JS/CSS -->
<!-- Pos: 前端源码目录 -->

# `apps/web/src/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- Vue 3 应用源代码，包含入口、全局样式、路由与页面组件。
- 使用 Composition API (`<script setup>`)。

## 文件
| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `main.ts` | 入口 | 初始化 Vue 应用、挂载 Router 与全局样式。 |
| `App.vue` | 根组件 | 提供全局布局（Header）与 RouterView 容器。 |
| `style.css` | 全局样式 | 定义 CSS 变量与通用 UI 组件样式（Button, Card）。 |
| `router/` | 路由 | 路由定义。 |
| `views/` | 视图 | 页面级组件（对话/配置；对话页支持工具调用实时进度展示）。 |
| `assets/` | 资源 | 静态图片/样式资源。 |
| `components/` | 组件 | 通用 UI 组件（如 `TimeSeriesChart.vue` 折线图、`KG3DGraph.vue` 2D 知识图谱）。 |
