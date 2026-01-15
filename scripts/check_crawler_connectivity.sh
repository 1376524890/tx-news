#!/usr/bin/env bash
# Input: config/sources.txt +（可选）docker compose 运行中的 collector 容器 + 出网权限
# Output: 爬虫（sources）连通性诊断（DNS/TLS/HTTP；可选 NATS/基础设施 TCP）
# Pos: 运维排障脚本（爬虫连通性）（变更时同步更新以上注释与所属目录 FOLDER.md）
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

usage() {
  cat <<'EOF'
Usage:
  bash scripts/check_crawler_connectivity.sh [--sources PATH] [--limit N] [--timeout SECONDS]
                                           [--container SERVICE] [--host-only] [--container-only]
                                           [--no-proxy] [--force-ipv4] [--dry-run]

Notes:
  - 默认同时做 host 与容器(collector)检查；容器检查需要 docker compose + 容器运行中。
  - 该脚本会对 sources 做 DNS/TLS/HTTP 探测；容器侧额外做 NATS/基础设施 TCP 探测。
  - --no-proxy 仅影响本脚本的 HTTP 探测（httpx trust_env=False）；不修改仓库代码。
  - --force-ipv4 会让 HTTP 探测只用 A 记录（避免容器 IPv6 不可达导致的误判）。
EOF
}

SOURCES_FILE="config/sources.txt"
LIMIT="0"
TIMEOUT="15"
CONTAINER_SERVICE="collector"
HOST_ONLY="0"
CONTAINER_ONLY="0"
NO_PROXY_MODE="0"
FORCE_IPV4="0"
DRY_RUN="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sources)
      SOURCES_FILE="${2:-}"; shift 2 ;;
    --limit)
      LIMIT="${2:-}"; shift 2 ;;
    --timeout)
      TIMEOUT="${2:-}"; shift 2 ;;
    --container)
      CONTAINER_SERVICE="${2:-}"; shift 2 ;;
    --host-only)
      HOST_ONLY="1"; shift ;;
    --container-only)
      CONTAINER_ONLY="1"; shift ;;
    --no-proxy)
      NO_PROXY_MODE="1"; shift ;;
    --force-ipv4)
      FORCE_IPV4="1"; shift ;;
    --dry-run)
      DRY_RUN="1"; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      exit 2 ;;
  esac
done

if [[ "${HOST_ONLY}" == "1" && "${CONTAINER_ONLY}" == "1" ]]; then
  echo "Invalid flags: --host-only and --container-only cannot be used together." >&2
  exit 2
fi

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ ! -f "${SOURCES_FILE}" ]]; then
  echo "Missing sources file: ${SOURCES_FILE}" >&2
  exit 2
fi

if [[ "${DRY_RUN}" == "1" ]]; then
  echo "dry-run=1"
  echo "sources_file=${SOURCES_FILE}"
  echo "limit=${LIMIT} timeout=${TIMEOUT} no_proxy=${NO_PROXY_MODE} force_ipv4=${FORCE_IPV4}"
  echo "container_service=${CONTAINER_SERVICE} host_only=${HOST_ONLY} container_only=${CONTAINER_ONLY}"
  echo "sources_preview:"
  if have rg; then
    # shellcheck disable=SC2002
    cat "${SOURCES_FILE}" | rg -v '^\s*(#|$)' | head -n $(( LIMIT > 0 ? LIMIT : 20 )) || true
  else
    # shellcheck disable=SC2002
    cat "${SOURCES_FILE}" | grep -vE '^\s*(#|$)' | head -n $(( LIMIT > 0 ? LIMIT : 20 )) || true
  fi
  exit 0
fi

python_diag='
import os, socket, ssl, time
from urllib.parse import urlparse

timeout=float(os.environ.get("TIMEOUT","15").strip() or "15")
limit=int(os.environ.get("LIMIT","0").strip() or "0")
sources_file=os.environ.get("SOURCES_FILE","config/sources.txt")
no_proxy=(os.environ.get("NO_PROXY_MODE","0")=="1")
force_ipv4=(os.environ.get("FORCE_IPV4","0")=="1")

print("sources_file=", sources_file)
print("limit=", limit, "timeout=", timeout, "no_proxy_mode=", no_proxy, "force_ipv4=", force_ipv4)
print("HTTP_PROXY=", os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "")
print("HTTPS_PROXY=", os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "")
print("NO_PROXY=", os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "")

try:
    import httpx
except Exception as e:
    httpx=None
    print("WARN: httpx not available:", type(e).__name__, e)

def print_mtu():
    # Read-only best-effort. Useful for diagnosing Docker MTU blackholes (TLS handshake timeouts).
    for iface in ("eth0", "ens3", "en0", "wlan0", "docker0", "lo"):
        p=f"/sys/class/net/{iface}/mtu"
        try:
            with open(p, "r", encoding="utf-8") as f:
                mtu=f.read().strip()
            if mtu:
                print("mtu:", iface, mtu)
        except Exception:
            pass

def read_sources(path: str):
    urls=[]
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s=line.strip()
            if not s or s.startswith("#"):
                continue
            urls.append(s)
    return urls

def _sockaddr(addr: str, port: int):
    return (addr, port, 0, 0) if ":" in addr else (addr, port)

def resolve(host: str, port: int):
    infos=socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    addrs=[]
    for i in infos:
        a=i[4][0]
        if a not in addrs:
            addrs.append(a)
    v4=[a for a in addrs if ":" not in a]
    v6=[a for a in addrs if ":" in a]
    return addrs, v4, v6

def tls_probe(host: str, port: int, addr: str):
    ctx=ssl.create_default_context()
    family = socket.AF_INET6 if ":" in addr else socket.AF_INET
    s=ctx.wrap_socket(socket.socket(family=family), server_hostname=host)
    s.settimeout(timeout)
    t0=time.time()
    try:
        s.connect(_sockaddr(addr, port))
        return True, (time.time()-t0), (s.version() or ""), (s.cipher() or ("","",""))[0]
    except Exception as e:
        return False, (time.time()-t0), type(e).__name__, str(e)
    finally:
        try:
            s.close()
        except Exception:
            pass

def http_probe(url: str):
    if httpx is None:
        return False, 0.0, "httpx-missing", ""
    headers={"User-Agent":"tx-news-check/0.1 (+https://example.invalid)"}
    t0=time.time()
    old_getaddrinfo = socket.getaddrinfo
    if force_ipv4:
        def _ga(host, port, family=0, type=0, proto=0, flags=0):
            infos=old_getaddrinfo(host, port, family, type, proto, flags)
            return [i for i in infos if i[0] == socket.AF_INET]
        socket.getaddrinfo = _ga
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=(not no_proxy)) as client:
            with client.stream("GET", url, headers=headers) as r:
                status=r.status_code
                final=str(r.url)
                return True, (time.time()-t0), str(status), final
    except Exception as e:
        return False, (time.time()-t0), type(e).__name__, str(e)
    finally:
        socket.getaddrinfo = old_getaddrinfo

urls=read_sources(sources_file)
if limit > 0:
    urls=urls[:limit]
print("urls=", len(urls))
print_mtu()

tls_v4_ok=tls_v4_timeout=tls_v4_err=0
tls_v6_unreach=tls_v6_ok=tls_v6_err=0
http_ok=http_timeout=http_err=0

for url in urls:
    u=urlparse(url)
    host=(u.hostname or "").strip()
    port=int(u.port or (443 if u.scheme=="https" else 80))
    print()
    print("==", url)
    if not host:
        print("invalid_url: missing host")
        continue
    try:
        addrs, v4, v6 = resolve(host, port)
        print("dns:", "v4=", len(v4), "v6=", len(v6), "addrs=", addrs[:6])
    except Exception as e:
        print("dns_error:", type(e).__name__, e)
        continue

    if u.scheme == "https":
        if v4:
            # Try up to 2 A records; some sites have partial edge reachability.
            tried=False
            for addr in v4[:2]:
                ok, dt, a, b = tls_probe(host, port, addr)
                print("tls_v4:", "ok" if ok else "err", "addr=", addr, "t=", f"{dt:.2f}s", a, b)
                tried=True
                if ok:
                    tls_v4_ok += 1
                    break
                if a == "TimeoutError":
                    tls_v4_timeout += 1
                else:
                    tls_v4_err += 1
            if not tried:
                print("tls_v4: skip (no A record)")
        else:
            print("tls_v4: skip (no A record)")
        if v6:
            ok, dt, a, b = tls_probe(host, port, v6[0])
            print("tls_v6:", "ok" if ok else "err", "addr=", v6[0], "t=", f"{dt:.2f}s", a, b)
            if ok:
                tls_v6_ok += 1
            elif a == "OSError" and "Network is unreachable" in b:
                tls_v6_unreach += 1
            else:
                tls_v6_err += 1
        else:
            print("tls_v6: skip (no AAAA record)")
    else:
        print("tls: skip (scheme != https)")

    ok, dt, a, b = http_probe(url)
    if ok:
        print("http:", "ok", "t=", f"{dt:.2f}s", "status=", a, "final=", b)
        http_ok += 1
    else:
        print("http:", "err", "t=", f"{dt:.2f}s", "type=", a, "detail=", b)
        if a in ("ConnectTimeout", "ReadTimeout", "TimeoutError"):
            http_timeout += 1
        else:
            http_err += 1

print()
print("== Summary ==")
print("tls_v4_ok=", tls_v4_ok, "tls_v4_timeout=", tls_v4_timeout, "tls_v4_err=", tls_v4_err)
print("tls_v6_ok=", tls_v6_ok, "tls_v6_unreach=", tls_v6_unreach, "tls_v6_err=", tls_v6_err)
print("http_ok=", http_ok, "http_timeout=", http_timeout, "http_err=", http_err)
if tls_v6_unreach > 0 and not force_ipv4:
    print("hint: IPv6 is unreachable here; consider re-running with --force-ipv4 to reduce noise.")
if tls_v4_timeout > 0:
    print("hint: TLS handshake timeouts usually indicate outbound path issues (common: Docker MTU mismatch / egress filtering).")

infra=os.environ.get("INFRA_TARGETS","").strip()
if infra:
    print()
    print("== Infra TCP check ==")
    for item in [x.strip() for x in infra.split(",") if x.strip()]:
        if ":" not in item:
            print("skip:", item, "(missing host:port)")
            continue
        ih, ip = item.rsplit(":", 1)
        try:
            ipn=int(ip)
        except Exception:
            print("skip:", item, "(invalid port)")
            continue
        try:
            socket.getaddrinfo(ih, ipn, proto=socket.IPPROTO_TCP)
            t0=time.time()
            s=socket.create_connection((ih, ipn), timeout=timeout)
            s.close()
            print("tcp:", "ok", item, "t=", f"{time.time()-t0:.2f}s")
        except Exception as e:
            print("tcp:", "err", item, type(e).__name__, e)

if os.environ.get("CHECK_NATS","0")=="1":
    print()
    print("== NATS JetStream check ==")
    nats_url=os.environ.get("NATS_URL") or os.environ.get("TXNEWS_NATS_URL") or ""
    stream=os.environ.get("NATS_STREAM") or os.environ.get("TXNEWS_NATS_STREAM") or "txnews"
    print("nats_url=", nats_url, "stream=", stream)
    try:
        import asyncio
        import nats  # type: ignore[import-not-found]
    except Exception as e:
        print("nats_import_error:", type(e).__name__, e)
    else:
        async def _run():
            nc=await nats.connect(nats_url, connect_timeout=timeout)
            try:
                js=nc.jetstream()
                await js.stream_info(stream)
                ack=await js.publish(f"{stream}.health", b"{}", timeout=timeout)
                print("jetstream:", "ok", "ack=", getattr(ack, "seq", None))
            finally:
                await nc.drain()
        try:
            asyncio.run(_run())
        except Exception as e:
            print("jetstream_error:", type(e).__name__, e)
'

echo
if [[ "${CONTAINER_ONLY}" != "1" ]]; then
  echo "== Host check =="
  SOURCES_FILE="${SOURCES_FILE}" LIMIT="${LIMIT}" TIMEOUT="${TIMEOUT}" NO_PROXY_MODE="${NO_PROXY_MODE}" FORCE_IPV4="${FORCE_IPV4}" \
    python -c "${python_diag}"
fi

if [[ "${HOST_ONLY}" == "1" ]]; then
  exit 0
fi

echo
echo "== Container(${CONTAINER_SERVICE}) check =="
if compose exec -T "${CONTAINER_SERVICE}" python -c "import sys; print('python=',sys.version.split()[0])" >/dev/null 2>&1; then
  compose exec -T "${CONTAINER_SERVICE}" /bin/sh -lc 'echo "resolv.conf:"; cat /etc/resolv.conf || true; echo; echo "HTTP_PROXY=${HTTP_PROXY:-${http_proxy:-}}"; echo "HTTPS_PROXY=${HTTPS_PROXY:-${https_proxy:-}}"; echo "NO_PROXY=${NO_PROXY:-${no_proxy:-}}"' || true

  # NOTE: crawler 本身依赖这些服务；若这里不通，collector 即使能抓到网页也会卡在写入/发布。
  INFRA_TARGETS="postgres:5432,nats:4222,minio:9000,redis:6379,qdrant:6333"
  compose exec -T "${CONTAINER_SERVICE}" env \
    SOURCES_FILE="${SOURCES_FILE}" \
    LIMIT="${LIMIT}" \
    TIMEOUT="${TIMEOUT}" \
    NO_PROXY_MODE="${NO_PROXY_MODE}" \
    FORCE_IPV4="${FORCE_IPV4}" \
    INFRA_TARGETS="${INFRA_TARGETS}" \
    CHECK_NATS=1 \
    python -c "${python_diag}" || true
else
  echo "Skipped: cannot exec into container '${CONTAINER_SERVICE}' from this shell."
fi
