<!-- Input: URL 路径 -->
<!-- Output: 组件渲染指令 -->
<!-- Pos: 前端路由配置 -->

# `apps/web/src/router/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 使用 `vue-router` 管理 SPA 路由。
- 采用 History 模式（依赖后端 Rewrite 支持）。

## 文件
| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `index.ts` | 路由表 | 构建模式区分：public UI `/`（对话）+ `/dashboard`（看板）+ `/config`（配置）；admin UI `/`（配置）。 |
