<!-- Input: Vue 3 + TypeScript 源码 -->
<!-- Output: 构建产物 `dist/` 由 `apps/api/main.py` 挂载 -->
<!-- Pos: 前端工程根目录（变更时同步更新以上注释与本文件内容） -->

# `apps/web/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- Vue 3 + Vite + TypeScript 单页应用（SPA）。
- 替代原 `apps/api/static`，提供更丰富的交互与动态更新。
- 构建后产物位于 `dist/`，由 FastAPI 静态挂载。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `src/main.ts` | 入口 | 挂载 Vue 应用与 Router。 |
| `src/router/` | 路由 | 定义 `/` (Chat)、`/dashboard` (看板) 与 `/admin` (管理台) 路由。 |
| `src/views/` | 页面 | `ChatView.vue`（对话）、`DashboardView.vue`（看板）、`AdminView.vue`（管理台）。 |
| `src/components/` | 组件 | 通用组件（如 `TimeSeriesChart.vue` 折线图）。 |
| `src/style.css` | 样式 | 全局样式（移植自原 static/style.css）。 |
| `package.json` | 依赖 | Vue, Vite, vue-router, marked 等。 |
| `vite.config.ts` | 配置 | 开发代理与构建配置。 |
