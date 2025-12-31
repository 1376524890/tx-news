<!-- Input: Vue 3 + TypeScript 源码 -->
<!-- Output: 构建产物 `dist_public/` 与 `dist_admin/`（分别由 8000/8001 挂载） -->
<!-- Pos: 前端工程根目录（变更时同步更新以上注释与本文件内容） -->

# `apps/web/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- Vue 3 + Vite + TypeScript 单页应用（SPA）。
- 替代原 `apps/api/static`，提供更丰富的交互与动态更新。
- 构建后产物位于 `dist_public/`（用户对话）与 `dist_admin/`（配置页），分别由 8000/8001 服务挂载。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `README.md` | 说明文档 | 前端构建/开发说明。 |
| `src/main.ts` | 入口 | 挂载 Vue 应用与 Router。 |
| `src/router/` | 路由 | 构建模式区分：public UI `/`（对话）；admin UI `/`（配置）。 |
| `src/views/` | 页面 | `ChatView.vue`（对话）、`ConfigView.vue`（配置）；`DashboardView.vue`/`AdminView.vue` 为 legacy（不在默认路由中暴露）。 |
| `src/components/` | 组件 | 通用组件（如 `TimeSeriesChart.vue` 折线图）。 |
| `src/style.css` | 样式 | 全局样式（移植自原 static/style.css）。 |
| `package.json` | 依赖 | Vue, Vite, vue-router, marked 等。 |
| `vite.config.ts` | 配置 | 开发代理与构建配置。 |
