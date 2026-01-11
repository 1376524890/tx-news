<!-- Input: Docker 打包/编排需求（多端部署/可移植性） -->
<!-- Output: docker/ 目录结构与文件职责清单 -->
<!-- Pos: Docker 目录索引文档（变更时同步更新以上注释与本文件内容） -->

# `docker/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 使用一个多目标 `Dockerfile` 同构构建多个“功能镜像”，按服务拆分运行（API/Admin/Worker/Collector/NATS-Bridge）。
- 前端 `apps/web` 作为构建阶段产物拷贝进 API/Admin 镜像，不依赖宿主机 Node.js。
- 运行态配置通过环境变量与挂载 `config/` 注入；vLLM 按原有逻辑在 Docker 外单独运行，主程序通过 `TXNEWS_LLM_DEEP_BASE_URL`（默认 `host.docker.internal`）连接。
 - 可选构建加速：通过 build args 支持 pip/npm/apt 国内镜像（见 `.env.example` 的 `TXNEWS_*_MIRROR` 配置）。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `Dockerfile` | 构建定义 | 多目标构建：`api`/`admin`/`worker`/`collector`/`nats-bridge`。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
