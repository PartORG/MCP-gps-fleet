# Fleet MCP server image: `fleet-mcp` over Streamable HTTP on port 8000.
#
#   docker build -t fleet-mcp .
#
# The data is NOT in the image.  At start, `fleet-seed` writes fleet.db (synthetic
# data ending "now", so it must be fresh) and `fleet-ingest` writes kb.db (needs a
# reachable Ollama).  compose.yaml and the Helm chart run those two first.
FROM python:3.14-slim

WORKDIR /app
# Compile .pyc files at build time (faster container start); copy files out of uv's
# cache instead of hard-linking (the cache is a build-time mount, not in the image).
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# 1) Dependencies only.  This layer is rebuilt only when pyproject.toml / uv.lock change,
#    not on every code edit.  --locked: fail if uv.lock is out of date instead of updating it.
COPY pyproject.toml uv.lock ./
# Two build-time mounts, neither ends up in the image:
#   uv binary  : taken from uv's official image only for this step (saves 54 MB)
#   uv cache   : stays on the build machine; rebuilds reuse it (it added ~600 MB before)
RUN --mount=from=ghcr.io/astral-sh/uv:0.12,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# 2) The project: code and the knowledge-base markdown (README is referenced by pyproject).
COPY README.md ./
COPY src ./src
COPY kb ./kb
RUN --mount=from=ghcr.io/astral-sh/uv:0.12,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

ENV PATH=/app/.venv/bin:$PATH \
    FLEET_MODELS_DIR=/app/models \
    FLEET_TRANSPORT=http \
    FLEET_HOST=0.0.0.0

# 3) Bake the reranker model in (~22 MB at build time) instead of downloading it at every
#    start.  It lives in /app/models, outside /app/data, which is a volume at runtime.
RUN python -c "from fleet_mcp.rag import _reranker; _reranker()"

# Run unprivileged.  The process may write only to /app/data (the databases).
RUN useradd --uid 10001 --no-create-home app && mkdir data && chown app data
USER 10001

EXPOSE 8000
# FLEET_HOST=0.0.0.0 makes fleet-mcp refuse to start without FLEET_API_TOKEN.
CMD ["fleet-mcp"]
