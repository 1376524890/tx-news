<!-- Input: 浏览器访问 /static 下的前端资源 (已弃用) -->
<!-- Output: 简单 UI（对话页/管理台）的静态文件 -->
<!-- Pos: 前端静态资源索引 (Legacy) -->

# `apps/api/static/` 目录 (已弃用)

> 注意：该目录包含旧版的静态 HTML/JS 文件。
> 当前系统已迁移至 `apps/web/` (Vue 3 + TypeScript SPA)。
> 该目录下的文件目前仅供参考，或在 `apps/web/dist_public`/`apps/web/dist_admin` 未生成时作为备选。

架构（≤3行）：
- 纯静态页面（无构建/无 Node），曾由 `apps/api/main.py` 挂载。
- 已由 `apps/web/` 的 Vue SPA 替代。

## 文件
| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `index.html` | Legacy UI | 旧对话页入口（曾挂载在 `/`）。 |
| `app.js` | Legacy UI | 旧对话页逻辑（含 SSE/工具调用渲染）。 |
| `admin.html` | Legacy UI | 旧管理台入口（曾挂载在 `/admin`）。 |
| `admin.js` | Legacy UI | 旧管理台逻辑（依赖 `/admin/*` 系列 API）。 |
| `style.css` | Legacy UI | 旧版统一样式。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
