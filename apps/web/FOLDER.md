<!-- Input: Vue 3 + TypeScript 源码 -->
<!-- Output: 构建产物 `dist_public/` 与 `dist_admin/`（分别由 8000/8001 挂载） -->
<!-- Pos: 前端工程根目录（变更时同步更新以上注释与本文件内容） -->

# `apps/web/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- Vue 3 + Vite + TypeScript 单页应用（SPA）。
- 替代原 `apps/api/static`，提供更丰富的交互与动态更新。
- 构建后产物位于 `dist_public/`（对话/看板/配置入口）与 `dist_admin/`（可选独立配置页），分别由 8000/8001 服务挂载。
- 看板页包含二维知识图谱可视化组件（SVG），用于实时展示 KG 节点与连接关系。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `README.md` | 说明文档 | 前端构建/开发说明。 |
| `src/main.ts` | 入口 | 挂载 Vue 应用与 Router。 |
| `src/router/` | 路由 | 构建模式区分：public UI `/`（对话）+ `/dashboard`（看板）+ `/config`（配置）；admin UI `/`（配置）。 |
| `src/views/` | 页面 | `ChatView.vue`（对话）、`DashboardView.vue`（看板）、`ConfigView.vue`（配置）。 |
| `src/components/` | 组件 | 通用组件（如 `TimeSeriesChart.vue` 折线图、`KG3DGraph.vue` 2D 知识图谱）。 |
| `src/style.css` | 样式 | 全局样式（移植自原 static/style.css）。 |
| `package.json` | 依赖 | Vue, Vite, vue-router, marked 等。 |
| `package-lock.json` | 锁文件 | 固化 npm 依赖解析；用于 Docker `npm ci` 的确定性构建。 |
| `vite.config.ts` | 配置 | 开发代理/构建配置与 dev `allowedHosts`，包含 `/dashboard` 等 API 代理（可用 `TXNEWS_VITE_ALLOWED_HOSTS` 追加）。 |
