#!/usr/bin/env bash
# Input: .env (LLM base_url/model/api_key) + outbound network access
# Output: host vs container connectivity diagnostics for online LLM (DNS/TLS/HTTP)
# Pos: 运维排障脚本（对话在线 LLM 连通性）（变更时同步更新以上注释与所属目录 FOLDER.md）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

have() { command -v "$1" >/dev/null 2>&1; }

compose() {
  if have docker && docker info >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    docker compose "$@"
    return
  fi
  if have docker-compose; then
    docker-compose "$@"
    return
  fi
  echo "docker compose not available in this shell; container-side checks will be skipped." >&2
  return 2
}

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

BASE_URL="${TXNEWS_LLM_CHAT_BASE_URL:-${TXNEWS_LLM_BASE_URL:-}}"
MODEL="${TXNEWS_LLM_CHAT_MODEL_NAME:-${TXNEWS_LLM_MODEL_NAME:-qwen3-max}}"
API_KEY="${TXNEWS_LLM_CHAT_API_KEY:-${TXNEWS_LLM_API_KEY:-${DASHSCOPE_API_KEY:-}}}"

if [[ -z "${BASE_URL}" ]]; then
  echo "Missing LLM base_url: set TXNEWS_LLM_BASE_URL or TXNEWS_LLM_CHAT_BASE_URL in .env" >&2
  exit 1
fi
if [[ -z "${API_KEY}" ]]; then
  echo "Missing LLM api_key: set TXNEWS_LLM_API_KEY (or TXNEWS_LLM_CHAT_API_KEY / DASHSCOPE_API_KEY) in .env" >&2
  exit 1
fi

echo "LLM base_url=${BASE_URL}"
echo "LLM model=${MODEL}"
echo "LLM api_key=SET"

python_diag='
import json, os, socket, sys, time
import httpx

base_url=os.environ["BASE_URL"].rstrip("/")
model=os.environ["MODEL"]
api_key=os.environ["API_KEY"]

host=base_url.split("://",1)[-1].split("/",1)[0]
print(f"DNS host={host}")
try:
    infos=socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    addrs=sorted({i[4][0] for i in infos})
    print("DNS addrs:", addrs[:10])
except Exception as e:
    print("DNS error:", type(e).__name__, e)

url=f"{base_url}/chat/completions"
headers={"Authorization": f"Bearer {api_key}"}
body={"model": model, "messages":[{"role":"user","content":"ping"}], "temperature": 0}

timeout=httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=20.0)

def post(stream: bool):
    payload=dict(body)
    if stream:
        payload["stream"]=True
    with httpx.Client(timeout=timeout) as client:
        started=time.time()
        if stream:
            with client.stream("POST", url, headers=headers, json=payload) as r:
                print("stream status=", r.status_code)
                r.raise_for_status()
                # read a few lines then stop
                n=0
                for line in r.iter_lines():
                    if not line: continue
                    s=line.decode("utf-8") if isinstance(line,(bytes,bytearray)) else str(line)
                    if not s.strip(): continue
                    print("stream line:", s[:200])
                    n += 1
                    if n >= 3:
                        break
        else:
            r=client.post(url, headers=headers, json=payload)
            print("status=", r.status_code, "time_s=", round(time.time()-started,2))
            print("resp_head=", r.text[:200].replace("\\n"," "))

print("HTTP test: non-stream")
try:
    post(False)
except Exception as e:
    print("non-stream error:", type(e).__name__, e)

print("HTTP test: stream")
try:
    post(True)
except Exception as e:
    print("stream error:", type(e).__name__, e)
'

echo
echo "== Host check =="
BASE_URL="${BASE_URL}" MODEL="${MODEL}" API_KEY="${API_KEY}" python -c "${python_diag}"

echo
echo "== Container(api) check =="
if compose exec -T api python -c "import sys; print('python=',sys.version.split()[0])" >/dev/null 2>&1; then
  BASE_URL="${BASE_URL}" MODEL="${MODEL}" API_KEY="${API_KEY}" compose exec -T api python -c "${python_diag}" || true
else
  echo "Skipped: cannot exec into container 'api' from this shell."
fi
