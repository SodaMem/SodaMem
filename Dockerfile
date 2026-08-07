# syntax=docker/dockerfile:1
#
# SodaMem self-hosted server image.
#
#   docker build -t sodamem .
#   docker run -e SODAMEM_API_KEY=... -p 8000:8000 -v sodamem-data:/data sodamem
#
# Ships the REST API on / and the web console on /console.
# See docker-compose.yml for the one-command path (PRD R1.9).

########################################
# Stage 1: console-builder — compile the web console to static assets.
#
# In its own stage so node/npm and a ~200MB node_modules tree never reach the
# runtime image; only the handful of hashed files in `dist/` cross over.
#
# The console is genuinely optional at RUNTIME (server/console_mount.py logs
# and carries on when `console/dist` is absent), but it is NOT optional here:
# a self-hosted image that answers `docker compose up` with an API and no
# console is not what the README promises. Building it in means the promise
# and the artifact agree.
########################################
FROM node:22-slim AS console-builder

WORKDIR /console

# Lockfile first: `npm ci` is its own cached layer, re-run only when deps move.
COPY console/package.json console/package-lock.json ./
RUN npm ci

COPY console/ ./
RUN npm run build

########################################
# Stage 2: builder — resolve deps with uv, build a non-editable venv.
########################################
FROM python:3.12-slim-bookworm AS builder

# build-essential covers native extensions (e.g. chromadb's hnswlib) on
# platforms/python combos without a prebuilt wheel. Builder-only: none of
# this reaches the final image, just the venv it produces.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Lockfile + package metadata first so dependency resolution is its own
# cached layer, invalidated only when these actually change.
COPY pyproject.toml uv.lock README.md ./
COPY sodamem ./sodamem
COPY mcp_server ./mcp_server
COPY server ./server

# --no-dev: skip test/lint tooling. --no-editable: bake the packaged Python
# sources into site-packages as a real install, not path references to /app —
# the final stage does not carry the builder's source tree.
# extras: [server] = FastAPI/uvicorn (the ASGI stack, base install stays free
# of it per I1); [chroma] = vector search + default embedder; [llm] = the
# OpenAI-wire-compatible provider ingest/answer need.
RUN uv sync --frozen --no-dev --no-editable \
        --extra server --extra chroma --extra llm

########################################
# Stage 3: runtime — slim image, non-root, pre-warmed embedder cache.
########################################
FROM python:3.12-slim-bookworm AS runtime

RUN groupadd --system sodamem \
    && useradd --system --gid sodamem --create-home \
        --home-dir /home/sodamem --shell /usr/sbin/nologin sodamem

WORKDIR /app

ENV PATH=/app/.venv/bin:$PATH \
    HOME=/home/sodamem \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SODAMEM_DATA_ROOT=/data \
    SODAMEM_PORT=8000

# --chown at COPY time, not a follow-up `RUN chown -R`: a recursive chown
# over an already-copied 300MB+ venv forces overlayfs to copy-up every file
# again for the metadata change, silently doubling that layer's size. Setting
# ownership as part of the copy is the same end state for free.
COPY --from=builder --chown=sodamem:sodamem /app/.venv /app/.venv
# Keep the runtime-stage source copy even though `server` is installed in the
# venv. `/app/server` intentionally shadows that installed package so
# server/console_mount.py's parent-relative lookup resolves /app/console/dist;
# uvicorn continues to run `server.app:build --factory` in this layout.
COPY --chown=sodamem:sodamem server ./server
# server/console_mount.py resolves `Path(__file__).parent.parent/console/dist`,
# so /app/server -> /app/console/dist. This path is the contract between the
# two; moving either side without the other silently un-mounts the console
# (INFO log, API still fine) rather than failing the build.
COPY --from=console-builder --chown=sodamem:sodamem /console/dist ./console/dist

# Just the empty mount point — cheap, no venv tree underneath it to copy-up.
RUN mkdir -p /data && chown sodamem:sodamem /data

USER sodamem

# --- Pre-warm the ONNX MiniLM embedder cache --------------------------------
# chromadb's default embedder (ONNXMiniLM_L6_V2, wrapped by
# sodamem.embedding.OnnxMiniLmEmbedder) downloads a ~90MB model tarball from
# S3 into `~/.cache/chroma/onnx_models` on first use — the path is hardcoded
# to Path.home() with no env override, so it has to be warmed as this exact
# user (HOME=/home/sodamem) for the cache to land where the app will look.
#
# Choice: bake it into the image at BUILD time rather than let the first
# request pay for it, because:
#   - self-hosted boxes are frequently egress-restricted; a first request
#     that silently needs an S3 pull can hang or hard-fail instead of just
#     being slow, and that failure mode is much harder to diagnose than a
#     slightly bigger image.
#   - "docker compose up" then an immediate health/search call should not
#     eat a multi-second, network-dependent surprise — mem0/Graphiti-style
#     one-command self-hosting implies the container is ready once it's up.
# Trade-off accepted: ~90MB heavier image for a deterministic, offline-capable
# cold start. If this ever needs to shrink, move this RUN behind a build arg
# and document that first-request latency moves to runtime instead.
# The download is lazy inside chromadb's ONNXMiniLM_L6_V2 — it only fires on
# the first __call__/embed(), not on construction — so the warm-up has to
# actually embed something, not just instantiate the wrapper.
RUN python -c "from sodamem.embedding.onnx_minilm import OnnxMiniLmEmbedder; OnnxMiniLmEmbedder().embed(['warmup'])"

EXPOSE 8000

# No curl/wget in slim — probe with the stdlib instead. Unauthenticated by
# design (server/app.py: /health touches no store, never gated by auth).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; port=os.environ.get('SODAMEM_PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3).status == 200 else 1)"

# Shell form (not exec-array) so ${SODAMEM_PORT} is expanded at container
# start — uvicorn's own --port flag is what actually binds; nothing in
# server/settings.py currently wires Settings.port to the bind address, so
# this is the one place that has to honor a non-default SODAMEM_PORT.
#
# `--workers 1` is EXPLICIT, and it is a CORRECTNESS constraint, not a
# performance trade-off (ADR 0001 §2). Per-user stores are SQLite databases
# opened without WAL; two uvicorn worker processes writing the same user's
# store corrupt it. Until now this held only because uvicorn's default
# happens to be 1 — an operator "tuning" throughput with --workers 4 would
# have silently destroyed user data. Stating it here makes the constraint
# visible at the exact place someone would edit it. Horizontal scaling needs
# an external job store + WAL first (ADR 0001, P2).
CMD ["sh", "-c", "exec uvicorn server.app:build --factory --workers 1 --host 0.0.0.0 --port ${SODAMEM_PORT:-8000}"]
