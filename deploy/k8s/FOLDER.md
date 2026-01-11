<!-- Input: Kubernetes 运行约束与本仓库服务拆分（api/admin/worker/collector/bridge） -->
<!-- Output: k8s/ 目录结构与清单职责说明 -->
<!-- Pos: Kubernetes 部署索引文档（变更时同步更新以上注释与本文件内容） -->

# `deploy/k8s/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- 以 “一个功能一个镜像/Deployment” 为默认拆分：`api`、`admin`、`worker`、`collector`、`nats-bridge`。
- 通过 ConfigMap/Secret 注入环境变量与 `config/` 文件（挂载到 `/app/config`）。
- 预留外部 vLLM：用 `TXNEWS_LLM_DEEP_BASE_URL` 指向集群内/外推理服务（例如 `http://vllm:9999/v1`）。

## 文件/子目录

| 路径 | 地位 | 功能 |
| --- | --- | --- |
| `base/` | Kustomize base | 最小可运行模板（需要你填入外部依赖的地址/密钥）。 |
| `README.md` | 使用说明 | 如何替换镜像、配置依赖、应用到集群。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |
