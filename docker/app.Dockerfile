# OceanTrace — ONE container: React UI + FastAPI + CPU ONNX, on one port.
#
# For hosts that run a single image with a single port and no reverse proxy:
# a Hugging Face Docker Space (the free SIH deployment) or any VM/PaaS.
# docker-compose.yml keeps the two-container layout for local work.
#
#   docker build -f docker/app.Dockerfile -t oceantrace .
#   docker run -p 7860:7860 --env-file .env oceantrace
#
# Data and weights are NOT in the image. At start the entrypoint pulls the
# demo bundle (OT_DATA_REPO, see scripts/deploy/fetch_data.py) or uses a
# bind-mounted /app/data, then checks the weights (scripts/get_weights.sh).

# --- stage 1: build the UI --------------------------------------------------
FROM node:20-alpine AS ui
WORKDIR /build
COPY main_system/frontend/package.json main_system/frontend/package-lock.json ./
RUN npm ci
COPY main_system/frontend/ ./
# Build-time UI settings (Vite inlines them). Empty = the app's defaults.
ARG VITE_DEMO_RUN_ID=""
ARG VITE_ENV="production"
ENV VITE_DEMO_RUN_ID=${VITE_DEMO_RUN_ID} VITE_ENV=${VITE_ENV}
RUN npm run build

# --- stage 2: API + the built UI --------------------------------------------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libexpat1: the rasterio wheel links against it and python:*-slim (Debian
# trixie) no longer ships it; without it the API dies importing rasterio.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces run the container as uid 1000; every other host is
# happy with that too, and nothing here needs root at runtime.
RUN useradd -m -u 1000 app

WORKDIR /app
COPY pyproject.toml README.md ./
RUN pip install . "huggingface_hub>=0.24"

COPY --chown=app:app contracts/ contracts/
COPY --chown=app:app main_system/ main_system/
COPY --chown=app:app analysis_engines/ analysis_engines/
COPY --chown=app:app scene_service/ scene_service/
COPY --chown=app:app metocean_service/ metocean_service/
COPY --chown=app:app ais_service/ ais_service/
COPY --chown=app:app scripts/ scripts/
COPY --chown=app:app .env.example ./
COPY --chown=app:app docker/app-entrypoint.sh docker/app-entrypoint.sh
COPY --from=ui --chown=app:app /build/dist /app/frontend_dist

RUN chmod +x docker/app-entrypoint.sh scripts/get_weights.sh \
    && mkdir -p data/runs main_system/backend/services/detection/weights \
    && chown -R app:app /app

USER app
ENV HOME=/home/app \
    DATA_ROOT=/app/data \
    OT_FRONTEND_DIST=/app/frontend_dist \
    PORT=7860

EXPOSE 7860

# /readyz, not /health: it also proves the registry DB answers. The long start
# period covers the first boot's bundle download.
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/readyz" || exit 1

# backend.main bootstraps sys.path itself; it only needs main_system/ as cwd.
WORKDIR /app/main_system
ENTRYPOINT ["/app/docker/app-entrypoint.sh"]
# Behind the host's TLS proxy: trust X-Forwarded-* so request.url is https.
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
