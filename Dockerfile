# syntax=docker/dockerfile:1

# ---- frontend build ----
FROM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Oracle Instant Client (enables python-oracledb thick mode, e.g. for 10G password verifiers) ----
FROM debian:trixie-slim AS instantclient
ARG TARGETARCH
ARG IC_VERSION=23.26.3.0.0
ARG IC_DIR=2326300
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && case "$TARGETARCH" in amd64) arch=x64 ;; arm64) arch=arm64 ;; *) echo "arch $TARGETARCH no soportada" >&2; exit 1 ;; esac \
    && curl -fsSL -o /tmp/ic.zip \
       "https://download.oracle.com/otn_software/linux/instantclient/${IC_DIR}/instantclient-basiclite-linux.${arch}-${IC_VERSION}.zip" \
    && mkdir -p /opt/oracle && unzip -q /tmp/ic.zip -d /opt/oracle \
    && mv /opt/oracle/instantclient_* /opt/oracle/instantclient

# ---- runtime ----
FROM python:3.12-slim
# libaio is required by Instant Client; Debian 13 ships it as libaio1t64 (libaio.so.1t64)
RUN apt-get update && apt-get install -y --no-install-recommends libaio1t64 \
    && ln -s "$(find /usr/lib -name 'libaio.so.1t64' | head -n1)" "$(dirname "$(find /usr/lib -name 'libaio.so.1t64' | head -n1)")/libaio.so.1" \
    && rm -rf /var/lib/apt/lists/*
COPY --from=instantclient /opt/oracle/instantclient /opt/oracle/instantclient
RUN echo /opt/oracle/instantclient > /etc/ld.so.conf.d/oracle-instantclient.conf && ldconfig
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    DBMON_DATA_DIR=/data \
    DBMON_STATIC_DIR=/app/static \
    DBMON_ORACLE_MODE=auto \
    DBMON_ORACLE_CLIENT_LIB_DIR=/opt/oracle/instantclient

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/app ./app
COPY --from=frontend /src/dist ./static

RUN useradd --system --uid 10001 --home-dir /app dbmon \
    && mkdir -p /data && chown dbmon:dbmon /data
USER dbmon

VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
