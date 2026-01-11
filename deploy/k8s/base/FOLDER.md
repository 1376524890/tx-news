<!-- Input: k8s base 资源清单（kustomize） -->
<!-- Output: base/ 下各资源文件职责说明 -->
<!-- Pos: Kubernetes base 索引（变更时同步更新以上注释与本文件内容） -->

# `deploy/k8s/base/` 目录

> 一旦我所属的文件夹有所变化，请更新我。

架构（≤3行）：
- Kustomize base：提供最小可运行模板，默认命名空间 `tx-news`。
- 通过 `tx-news-env`/`tx-news-secrets` 注入环境变量，通过 `tx-news-files` 挂载 `config.yaml`/`sources.txt` 到 `/app/config`。
- HuggingFace cache 通过 PVC `tx-news-hf-cache` 持久化挂载到 `/app/var/hf`（可按需替换为 emptyDir/更大存储）。

## 文件

| 文件 | 地位 | 功能 |
| --- | --- | --- |
| `kustomization.yaml` | kustomize 入口 | 组合 base 资源与镜像替换入口。 |
| `namespace.yaml` | 基础资源 | 创建 `tx-news` 命名空间。 |
| `configmap-env.yaml` | 配置 | Infra/LLM/HF 等环境变量（需要替换为你的地址）。 |
| `secret-env.yaml` | 密钥 | LLM API Key 等（需要填写/改为外部 Secret 管理）。 |
| `configmap-files.yaml` | 配置文件 | `config.yaml` 与 `sources.txt`（占位，需要替换）。 |
| `pvc-hf-cache.yaml` | 存储 | HuggingFace cache PVC（占位，可按集群存储类调整）。 |
| `api-deployment.yaml` | Deployment | API（8000；含 readiness/liveness）。 |
| `api-service.yaml` | Service | API ClusterIP。 |
| `admin-deployment.yaml` | Deployment | Admin（8001；可选）。 |
| `admin-service.yaml` | Service | Admin ClusterIP。 |
| `worker-deployment.yaml` | Deployment | Celery worker。 |
| `collector-deployment.yaml` | Deployment | Collector。 |
| `nats-bridge-deployment.yaml` | Deployment | NATS→Celery bridge。 |
| `_pod-template.yaml` | 参考 | 仅用于复用/拷贝的 Pod 模板（不参与 kustomize resources）。 |
| `FOLDER.md` | 目录文档 | 本目录的架构说明与文件职责清单。 |

