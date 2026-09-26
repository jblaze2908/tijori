# syntax=docker/dockerfile:1
# Build context is the repo root: `docker compose -f deploy/compose.yml build`.

FROM node:22.16.0-alpine@sha256:41e4389f3d988d2ed55392df4db1420ad048ae53324a8e2b7c6d19508288107e AS web
WORKDIR /web
COPY web/ ./
# No package.json yet means no UI: an empty dist, and the api serves the API alone.
RUN if [ -f package.json ]; then \
        corepack enable && pnpm install --frozen-lockfile --ignore-scripts && pnpm run build; \
    fi \
    && mkdir -p dist

FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# OS tools, not Python packages: pdftotext reads statements, qpdf decrypts them.
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils qpdf \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 tijori \
    && useradd --system --uid 10001 --gid tijori --home-dir /app --shell /usr/sbin/nologin tijori

WORKDIR /app
# Hash-pinned export of uv.lock (`uv export --no-dev`); pip refuses anything unhashed.
COPY api/requirements.lock.txt ./
RUN pip install --require-hashes --no-deps -r requirements.lock.txt

COPY api/alembic.ini ./
COPY api/migrations ./migrations
COPY api/tijori ./tijori
COPY --from=web /web/dist ./web
# Owned by the api user so a fresh named volume mounted here inherits it.
RUN install -d -o tijori -g tijori -m 0700 /data/blobs

ENV TIJORI_WEB_DIST=/app/web \
    TIJORI_BLOB_DIR=/data/blobs
USER tijori:tijori
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status != 200)"]
CMD ["uvicorn", "tijori.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
