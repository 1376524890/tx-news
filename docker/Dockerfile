# Input: 仓库源码 + requirements.txt + apps/web(Vite) 构建产物
# Output: 可复用的多目标镜像（api/admin/worker/collector/nats-bridge；Python 运行时固定 Debian suite 以避免 apt 404）
# Pos: Docker 构建定义（变更时同步更新以上注释与所属目录 FOLDER.md）

ARG PYTHON_VERSION=3.11
ARG NODE_VERSION=20

# Mirrors (optional; useful in CN networks)
ARG NPM_REGISTRY=
ARG PIP_INDEX_URL=
ARG PIP_EXTRA_INDEX_URL=
ARG PIP_TRUSTED_HOST=
ARG DEBIAN_MIRROR=
ARG DEBIAN_SECURITY_MIRROR=

FROM node:${NODE_VERSION}-alpine AS web-build
ARG NPM_REGISTRY
WORKDIR /app/apps/web
COPY apps/web/package*.json ./
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi
RUN npm ci --no-audit --no-fund
COPY apps/web/ ./
RUN npm run build:all


FROM python:${PYTHON_VERSION}-slim-bookworm AS py-runtime
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG PIP_TRUSTED_HOST
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ARG http_proxy
ARG https_proxy
ARG no_proxy
ARG DEBIAN_MIRROR
ARG DEBIAN_SECURITY_MIRROR
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Keep OS deps minimal; most Python deps use wheels.
RUN if [ -n "$DEBIAN_MIRROR" ]; then \
      if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
      fi; \
      if [ -f /etc/apt/sources.list ]; then \
        sed -i "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" /etc/apt/sources.list; \
      fi; \
    fi \
    && if [ -n "$DEBIAN_SECURITY_MIRROR" ]; then \
      if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|http://security.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
      fi; \
      if [ -f /etc/apt/sources.list ]; then \
        sed -i "s|http://security.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" /etc/apt/sources.list; \
      fi; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --default-timeout=60 --retries=10 --upgrade pip \
    && if [ -n "$PIP_INDEX_URL" ]; then python -m pip config set global.index-url "$PIP_INDEX_URL"; fi \
    && if [ -n "$PIP_EXTRA_INDEX_URL" ]; then python -m pip config set global.extra-index-url "$PIP_EXTRA_INDEX_URL"; fi \
    && if [ -n "$PIP_TRUSTED_HOST" ]; then python -m pip config set global.trusted-host "$PIP_TRUSTED_HOST"; fi \
    && pip install --no-cache-dir --default-timeout=60 --retries=10 -r requirements.txt

COPY src/ ./src/
COPY apps/ ./apps/
COPY config/ ./config/
COPY pyproject.toml README.md REQUIREMENTS.md ./

ENV PYTHONPATH=/app/src:/app

RUN useradd -m -u 10001 appuser \
    && mkdir -p /app/var/hf \
    && chown -R appuser:appuser /app
USER appuser


FROM py-runtime AS api
COPY --from=web-build /app/apps/web/dist_public /app/apps/web/dist_public
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]


FROM py-runtime AS admin
COPY --from=web-build /app/apps/web/dist_admin /app/apps/web/dist_admin
EXPOSE 8001
CMD ["python", "-m", "uvicorn", "apps.admin.main:app", "--host", "0.0.0.0", "--port", "8001"]


FROM py-runtime AS worker
CMD ["python", "-m", "celery", "-A", "tx_news.tasks.celery_app.celery_app", "worker", "-l", "INFO", "--pool=solo", "--concurrency=1"]


FROM py-runtime AS nats-bridge
CMD ["python", "-m", "apps.worker.nats_bridge"]


FROM py-runtime AS collector
CMD ["python", "-m", "apps.collector.main"]
