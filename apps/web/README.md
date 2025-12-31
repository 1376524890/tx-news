<!-- Input: Vue 3 + TypeScript 源码 + API 端点（public: /chat/stream,/status；admin: /api/config） -->
<!-- Output: 前端构建产物 `dist_public/` 与 `dist_admin/`（分别由 8000/8001 挂载） -->
<!-- Pos: 前端工程说明（变更时同步更新以上注释与所属目录 FOLDER.md） -->

# `apps/web/`（Vue 3 SPA）

该目录是 TX-News 的 v1 前端（Vue 3 + TypeScript + Vite）。为上线准备，前端会构建两个产物并由不同端口挂载：
- `dist_public/`：用户对话页（由 `apps/api/main.py` 在 `8000` 挂载）
- `dist_admin/`：配置页（由 `apps/admin/main.py` 在 `8001` 挂载）

说明：
- 若你通过 Cloudflare Tunnel / 反向代理只暴露一个端口（例如仅 `8000`），配置页可直接使用 `http://<host>:8000/config`（由 public SPA 路由提供），并通过同源 `/api/config` 保存“按用户在线 LLM 配置”。

## 本地构建

在仓库根目录执行：
```bash
npm --prefix apps/web install
npm --prefix apps/web run build:all
```

## 开发模式（可选）

```bash
npm --prefix apps/web run dev
```

说明：
- 生产环境由 FastAPI 直接提供静态资源：`8000` 挂载 `dist_public/`，`8001` 挂载 `dist_admin/`。
- 如需本地调试配置页：`npm --prefix apps/web run dev -- --mode admin`（否则默认为 public UI）。
