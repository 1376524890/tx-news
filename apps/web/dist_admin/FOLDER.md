<!-- Input: admin UI 构建输出 -->
<!-- Output: dist_admin/ 静态资源（HTML/CSS/JS/Assets） -->
<!-- Pos: admin UI 构建产物目录 -->

# `apps/web/dist_admin/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- admin UI 的 Vite 构建输出（独立配置入口）。
- API 进程可选择挂载本目录作为 8001 管理配置页面。
- 包含 `index.html` 与 Hash 化 `assets/`，并从 `public/` 复制资源。

## 文件
| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `assets/` | 静态资源 | 编译后的 JS/CSS 与图片等资源。 |
| `index.html` | 入口 | admin UI SPA 入口 HTML。 |
| `vite.svg` | 图标 | 来自 `public/` 的静态资源示例。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
