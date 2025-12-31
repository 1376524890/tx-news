<!-- Input: Vue 3 + TypeScript 源码 + API 端点（/chat/stream、/admin/status 等） -->
<!-- Output: 前端构建产物 `dist/`（由 FastAPI 挂载对外提供 UI） -->
<!-- Pos: 前端工程说明（变更时同步更新以上注释与所属目录 FOLDER.md） -->

# `apps/web/`（Vue 3 SPA）

该目录是 TX-News 的 v1 前端（Vue 3 + TypeScript + Vite）。构建产物 `dist/` 由 `apps/api/main.py` 挂载：
- `/`：对话页
- `/admin`：管理台
- `/dashboard`：看板

## 本地构建

在仓库根目录执行：
```bash
npm --prefix apps/web install
npm --prefix apps/web run build
```

## 开发模式（可选）

```bash
npm --prefix apps/web run dev
```

说明：
- 生产环境由 API 直接提供 `dist/` 静态资源；开发模式仅用于前端调试。
