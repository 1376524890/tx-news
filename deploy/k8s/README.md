<!-- Input: 使用者的 k8s 集群与镜像仓库 -->
<!-- Output: 可执行的部署步骤（kustomize） -->
<!-- Pos: Kubernetes 部署说明（变更时同步更新以上注释与所属目录 FOLDER.md） -->

# Kubernetes（kustomize）部署（预留）

该目录提供一个最小 `base` 模板，目标是让你可以在 K8s 上按功能水平扩展：
- `api`/`admin`：对外服务
- `worker`：Celery worker（可横向扩展，注意外部依赖容量与幂等策略）
- `collector`：抓取器（建议 1 副本或按 source 分片）
- `nats-bridge`：NATS→Celery 桥（通常 1 副本；JetStream durable 会做重投递）

## 使用

1) 构建并推送镜像（示例）：
- `tx-news-api`
- `tx-news-admin`
- `tx-news-worker`
- `tx-news-collector`
- `tx-news-nats-bridge`

2) 修改 `deploy/k8s/base/kustomization.yaml` 的 `images` 或用命令替换：
`kustomize edit set image tx-news-api=REGISTRY/tx-news-api:TAG`

3) 配置外部依赖与密钥：
- `deploy/k8s/base/configmap-env.yaml`：Postgres/Redis/NATS/MinIO/Qdrant 连接信息、`TXNEWS_LLM_DEEP_BASE_URL` 等
- `deploy/k8s/base/secret-env.yaml`：`DASHSCOPE_API_KEY`/`TXNEWS_LLM_API_KEY` 等
- `deploy/k8s/base/configmap-files.yaml`：`config.yaml` 与 `sources.txt`（按需替换为你自己的配置）

4) 应用到集群：
`kubectl apply -k deploy/k8s/base`

> vLLM 不随本仓库打包；建议在集群中单独部署 vLLM，并暴露成 Service，例如：`http://vllm:9999/v1`，再把 `TXNEWS_LLM_DEEP_BASE_URL` 指过去。
