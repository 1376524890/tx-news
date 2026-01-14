#!/usr/bin/env bash
# Input: .env (LLM base_url/model/api_key/proxy) + outbound network access
# Output: host vs container connectivity diagnostics for online LLM (DNS/TLS/HTTP/SSE)
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
  echo "WARN: Missing LLM api_key in .env (TXNEWS_LLM_API_KEY / TXNEWS_LLM_CHAT_API_KEY / DASHSCOPE_API_KEY)." >&2
  echo "      Will still run DNS/TLS tests; authenticated HTTP may fail with 401." >&2
fi

echo "LLM base_url=${BASE_URL}"
echo "LLM model=${MODEL}"
echo "LLM api_key=$([[ -n \"${API_KEY}\" ]] && echo SET || echo EMPTY)"
echo "HTTP_PROXY=${HTTP_PROXY:-${http_proxy:-}}"
echo "HTTPS_PROXY=${HTTPS_PROXY:-${https_proxy:-}}"
echo "NO_PROXY=${NO_PROXY:-${no_proxy:-}}"

python_diag='
import json, os, socket, ssl, sys, time
from urllib.parse import urlparse
import httpx

base_url=(os.environ.get("BASE_URL") or os.environ.get("TXNEWS_LLM_CHAT_BASE_URL") or os.environ.get("TXNEWS_LLM_BASE_URL") or "").strip().rstrip("/")
model=(os.environ.get("MODEL") or os.environ.get("TXNEWS_LLM_CHAT_MODEL_NAME") or os.environ.get("TXNEWS_LLM_MODEL_NAME") or "qwen3-max").strip()
api_key=(os.environ.get("API_KEY") or os.environ.get("TXNEWS_LLM_CHAT_API_KEY") or os.environ.get("TXNEWS_LLM_API_KEY") or os.environ.get("DASHSCOPE_API_KEY") or "").strip()

masked = (api_key[:4] + "…" + api_key[-4:]) if len(api_key) >= 10 else ("***" if api_key else "")
print("LLM resolved base_url=", base_url)
print("LLM resolved model=", model)
print("LLM resolved api_key_set=", bool(api_key), "api_key_masked=", masked)
print("trust_env proxy=", os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "")
print("trust_env no_proxy=", os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "")

if not base_url:
    print("Missing base_url in env (BASE_URL/TXNEWS_LLM_*).")
    raise SystemExit(2)

u=urlparse(base_url)
host=(u.hostname or "").strip()
port=int(u.port or (443 if u.scheme=="https" else 80))
scheme=(u.scheme or "").strip()
if not host:
    print("Invalid base_url:", base_url)
    raise SystemExit(2)

def _sockaddr(addr: str):
    return (addr, port, 0, 0) if ":" in addr else (addr, port)

def tls_probe_addr(addr: str):
    if scheme != "https":
        print("TLS probe skipped (scheme is not https).")
        return
    try:
        ctx=ssl.create_default_context()
        family = socket.AF_INET6 if ":" in addr else socket.AF_INET
        s=ctx.wrap_socket(socket.socket(family=family), server_hostname=host)
        s.settimeout(15)
        s.connect(_sockaddr(addr))
        print("TLS ok:", addr, s.version(), "cipher=", s.cipher())
        s.close()
    except Exception as e:
        print("TLS error:", addr, type(e).__name__, e)

print(f"DNS host={host}")
addrs=[]
try:
    infos=socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    # Keep original resolver order (do not sort), but de-duplicate.
    for i in infos:
        a=i[4][0]
        if a not in addrs:
            addrs.append(a)
    print("DNS addrs:", addrs[:10])
except Exception as e:
    print("DNS error:", type(e).__name__, e)

v4_addrs=[a for a in addrs if ":" not in a]
v6_addrs=[a for a in addrs if ":" in a]
print("DNS v4:", v4_addrs[:5])
print("DNS v6:", v6_addrs[:5])

print("TLS probe: v4")
for a in v4_addrs[:2] or [host]:
    tls_probe_addr(a)
print("TLS probe: v6")
for a in v6_addrs[:2]:
    tls_probe_addr(a)

timeout=httpx.Timeout(connect=30.0, read=60.0, write=60.0, pool=60.0)
headers={"Authorization": f"Bearer {api_key}"} if api_key else {}

def make_client(force: str | None):
    transport = None
    if force == "v4":
        transport = httpx.HTTPTransport(local_address="0.0.0.0")
    elif force == "v6":
        transport = httpx.HTTPTransport(local_address="::")
    return httpx.Client(timeout=timeout, trust_env=True, transport=transport)

def post_chat(stream: bool, *, force: str | None):
    url=f"{base_url}/chat/completions"
    payload={"model": model, "messages":[{"role":"user","content":"ping"}], "temperature": 0}
    if stream:
        payload["stream"]=True
    with make_client(force) as client:
        started=time.time()
        if stream:
            with client.stream("POST", url, headers=headers, json=payload) as r:
                tag = force or "auto"
                print(f"stream({tag}) status=", r.status_code)
                r.raise_for_status()
                # read a few lines then stop
                n=0
                for line in r.iter_lines():
                    if not line: continue
                    s=line.decode("utf-8") if isinstance(line,(bytes,bytearray)) else str(line)
                    if not s.strip(): continue
                    print(f"stream({tag}) line:", s[:200])
                    n += 1
                    if n >= 3:
                        break
        else:
            r=client.post(url, headers=headers, json=payload)
            tag = force or "auto"
            print(f"chat({tag}) status=", r.status_code, "time_s=", round(time.time()-started,2))
            print("resp_head=", r.text[:200].replace("\\n"," "))

def get_models(*, force: str | None):
    url=f"{base_url}/models"
    with make_client(force) as client:
        started=time.time()
        r=client.get(url, headers=headers)
        print("GET /models status=", r.status_code, "time_s=", round(time.time()-started,2))
        print("models_head=", r.text[:200].replace("\\n"," "))

print("HTTP test: GET /models")
try:
    get_models(force=None)
except Exception as e:
    print("models error:", type(e).__name__, e)

print("HTTP test: GET /models (-4)")
try:
    get_models(force="v4")
except Exception as e:
    print("models_v4 error:", type(e).__name__, e)

print("HTTP test: GET /models (-6)")
try:
    get_models(force="v6")
except Exception as e:
    print("models_v6 error:", type(e).__name__, e)

print("HTTP test: chat non-stream")
try:
    post_chat(False, force=None)
except Exception as e:
    print("chat error:", type(e).__name__, e)

print("HTTP test: stream")
try:
    post_chat(True, force=None)
except Exception as e:
    print("stream error:", type(e).__name__, e)

print("HTTP test: stream (force_ipv4=1)")
try:
    post_chat(True, force="v4")
except Exception as e:
    print("stream_ipv4 error:", type(e).__name__, e)

print("HTTP test: stream (force_ipv6=1)")
try:
    post_chat(True, force="v6")
except Exception as e:
    print("stream_ipv6 error:", type(e).__name__, e)
'

echo
echo "== Host check =="
BASE_URL="${BASE_URL}" MODEL="${MODEL}" API_KEY="${API_KEY}" python -c "${python_diag}"

echo
echo "== Container(api) check =="
if compose exec -T api python -c "import sys; print('python=',sys.version.split()[0])" >/dev/null 2>&1; then
  compose exec -T api /bin/sh -lc 'echo "resolv.conf:"; cat /etc/resolv.conf || true; echo; echo "HTTP_PROXY=${HTTP_PROXY:-${http_proxy:-}}"; echo "HTTPS_PROXY=${HTTPS_PROXY:-${https_proxy:-}}"; echo "NO_PROXY=${NO_PROXY:-${no_proxy:-}}"' || true
  compose exec -T api python -c "${python_diag}" || true
else
  echo "Skipped: cannot exec into container 'api' from this shell."
fi
