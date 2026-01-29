<!-- Input: 日常运维需求、日志与脚本 -->
<!-- Output: 常用命令、排查路径、修复步骤 -->
<!-- Pos: docs/ 运维说明（变更时同步更新以上注释与所属目录 FOLDER.md） -->

# 运维命令说明（Runbook）

本页用于本地/容器模式的常用运维命令与故障排查。  
所有命令默认在仓库根目录 `/home/codeserver/tx-news` 下执行。

## 1. 启动 / 停止

本地一键（推荐）
```bash
bash scripts/start_local.sh
bash scripts/stop_local.sh
```

Docker（仅启动基础设施）
```bash
docker compose up -d
docker compose down
```

Docker（含 app 服务）
```bash
docker compose --profile app up -d --build
docker compose --profile app down
```

## 2. 健康检查与核心端口

```bash
curl -sf http://127.0.0.1:18000/health
curl -sf http://127.0.0.1:8001/health
curl -sf http://127.0.0.1:9999/v1/models
```

服务端口参考：
- API: `18000`
- Admin: `8001`
- Web (dev): `8000`
- vLLM: `9999`
- NATS: `4222` / JetStream监控 `8222`
- Redis: `6379`
- Postgres: `5432`
- Qdrant: `6333`
- MinIO: `9000/9001`

## 3. 日志位置

本地脚本模式统一写入：
- `var/log/local/*.log`

常用：
- `worker.log` / `beat.log`
- `nats_bridge.log`
- `collector.log`
- `api.log` / `admin.log`
- `vllm.log`

建议用筛选：
```bash
rg -n "ERROR|Traceback|graphflow" var/log/local/worker.log
```

## 4. 常用排查命令

### 4.1 Celery / 队列堆积

检查 Redis 队列长度（没有 redis-cli 时使用 Python）：
```bash
python - <<'PY'
import redis
r=redis.Redis(host='localhost',port=6379,db=0)
print('default', r.llen('default'))
print('unacked', r.hlen('unacked'))
print('unacked_index', r.zcard('unacked_index'))
PY
```

如果 `default` 持续增长且 `worker.log` 没有任务输出，说明 worker 卡住或未启动。

处理建议：
1) 重启本地服务：`bash scripts/stop_local.sh && bash scripts/start_local.sh`
2) 只重启 Celery（进程级）：`pkill -f 'celery -A tx_news.tasks.celery_app.celery_app worker'` 然后重启本地脚本。

### 4.2 NATS / JetStream

查看 JetStream 状态：
```bash
curl -sf http://127.0.0.1:8222/jsz?streams=1
```

若 `nats` CLI 不存在（常见），请使用上述 HTTP 方式。

### 4.3 Postgres / Qdrant / MinIO

检查端口是否可用：
```bash
nc -z 127.0.0.1 5432 && echo "postgres ok"
nc -z 127.0.0.1 6333 && echo "qdrant ok"
nc -z 127.0.0.1 9000 && echo "minio ok"
```

### 4.4 Graph 构建流程跟踪

```bash
bash scripts/graph_trace.sh
```

## 5. 常见问题与修复

### Q1: 前端文章 / 分析 / 信号为 0
可能原因：
- Worker 未运行或被卡住
- 队列堆积但未被消费
- vLLM 不可用导致深分析没启动（仅影响分析，不影响入库）

排查步骤：
1) 看 `var/log/local/worker.log` 是否有 `Task ... received`  
2) 看 Redis 队列 `default` 是否在增长  
3) 重启本地服务（优先）

### Q2: Worker 卡住（队列不动）
表现：
- `default` 队列长度持续增加
- `unacked` 里有长时间未释放的任务

处理：
1) 重启 `stop_local.sh` / `start_local.sh`
2) 如需手动清理 unacked（慎用）：
```bash
python - <<'PY'
import redis, json
r=redis.Redis(host='localhost',port=6379,db=0)
items = r.hgetall('unacked')
for k, v in items.items():
    msg = json.loads(v)[0]
    task = msg.get('headers', {}).get('task')
    if task == 'tx_news.tasks.news_queue.queue_worker_task':
        tag = msg.get('properties', {}).get('delivery_tag')
        r.hdel('unacked', k)
        if tag:
            r.zrem('unacked_index', tag)
        print('cleared', task, msg.get('headers', {}).get('id'))
PY
```

### Q3: 分析不够“新鲜”
已实现策略：
- 分析队列按 `published_at / fetched_at` 倒序优先
- 超过 24 小时的文章直接清理（见 `news_queue.py`）

排查：
- 查看 worker 日志是否有 `added to analysis queue`  
- 查看 `queue_worker_task` 是否持续运行

### Q4: vLLM 没有被调用
检查：
```bash
curl -sf http://127.0.0.1:9999/v1/models
tail -n 200 var/log/local/vllm.log
```

确认 `TXNEWS_LLM_DEEP_BASE_URL` 指向本地 vLLM（`http://127.0.0.1:9999/v1`）。

## 6. 数据清理 / 重置

清空数据库和容器数据（0 数据启动）：
```bash
bash scripts/reset.sh
```

## 7. 运行时排障清单（快速版）

1) `docker compose ps` 确认 infra 都在 Running  
2) `ps -ef | rg "celery|nats_bridge|collector|uvicorn"` 确认本地进程  
3) `tail -n 200 var/log/local/worker.log` 观察任务流  
4) `python - <<'PY' ...` 查看 Redis 队列是否变化  
5) `bash scripts/graph_trace.sh` 查看 graph 生成链路

