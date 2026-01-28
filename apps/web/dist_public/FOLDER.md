<!-- Input: public UI 构建输出 -->
<!-- Output: dist_public/ 静态资源（HTML/CSS/JS/Assets） -->
<!-- Pos: public UI 构建产物目录 -->

# `apps/web/dist_public/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- public UI 的 Vite 构建输出（对话/看板/配置入口）。
- API 进程默认挂载本目录作为 `/`、`/dashboard`、`/config` 的前端静态资源。
- 包含 `index.html` 与 Hash 化 `assets/`，并从 `public/` 复制资源。

## 文件
| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `assets/` | 静态资源 | 编译后的 JS/CSS 与图片等资源。 |
| `index.html` | 入口 | public UI SPA 入口 HTML。 |
| `vite.svg` | 图标 | 来自 `public/` 的静态资源示例。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
