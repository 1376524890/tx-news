<!-- Input: 部署方式（Docker Compose / Kubernetes） -->
<!-- Output: deploy/ 目录结构与文件职责清单 -->
<!-- Pos: 部署目录索引文档（变更时同步更新以上注释与本文件内容） -->

# `deploy/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 该目录存放“部署侧”资产：Kubernetes（kustomize）等。
- 应用镜像由 `docker/Dockerfile` 构建；K8s 只负责运行与配置注入。
- vLLM 不在本目录打包，按外部依赖以 `TXNEWS_LLM_DEEP_BASE_URL` 方式接入。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |

## 子目录

| 子目录 | 地位 | 功能 |
| --- | --- | --- |
| `k8s/` | 部署清单 | Kustomize 基础清单与说明。 |
