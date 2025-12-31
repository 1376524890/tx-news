<!-- Input: 配置 UI（前端构建产物）+ Redis（用户配置） -->
<!-- Output: 8001 端口的配置服务（按用户保存在线 LLM 配置） -->
<!-- Pos: apps/admin 目录索引（变更时同步更新以上注释与本文件内容） -->

# `apps/admin/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 独立端口（默认 `8001`）提供“配置界面”，替代原先对外暴露的管理台 UI。
- 通过 cookie 标识用户，并将每个用户的在线 LLM 配置写入 Redis（用于 chat 按用户分摊成本）。
- 静态资源来自 `apps/web/dist_admin/`（Vite admin 构建产物）。
  - 备注：当部署环境仅暴露单端口（例如 Cloudflare Tunnel 只转发 `8000`）时，也可直接使用 `8000/config`（由 `apps/api` 的 public SPA 提供同源配置页与 `/api/config` 接口）。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `__init__.py` | 包标识 | 标记 `apps.admin` 为包。 |
| `main.py` | 服务入口 | FastAPI：提供配置 UI 与 `/api/config` 读写接口。 |
| `FOLDER.md` | 目录文档 | 本目录架构与文件职责清单。 |
