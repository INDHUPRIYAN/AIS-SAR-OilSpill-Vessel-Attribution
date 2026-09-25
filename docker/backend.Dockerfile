# OceanTrace backend — FastAPI + CPU ONNX Runtime.
#
# Build (from the repo root — the context must be the repo root):
#   docker build -f docker/backend.Dockerfile -t oceantrace-backend .
#
# Model weights are NOT baked into this image. The entrypoint runs
# scripts/get_weights.sh at container start (design doc Part VII: "weights
# pulled at startup, version logged"), so set OILGUARD_WEIGHTS_URL and mount a
# volume at the weights dir to avoid re-downloading on every start. If the
# download fails the API still boots — /detect degrades to the
# threshold_fallback engine by design.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# curl for the weight download; everything else ships as manylinux wheels
# (rasterio bundles GDAL, opencv-python-headless needs no libGL).
# libexpat1: the rasterio wheel links against it and python:*-slim (Debian
# trixie) no longer ships it; without it the API dies importing rasterio.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates libexpat1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer first so code edits don't re-resolve pip. pyproject installs
# dependencies only (packages = []), which is exactly what we want here.
COPY pyproject.toml README.md ./
RUN pip install .

# The code. .dockerignore keeps data/, weights, node_modules and .env out.
COPY contracts/ contracts/
COPY main_system/ main_system/
COPY analysis_engines/ analysis_engines/
COPY scene_service/ scene_service/
COPY metocean_service/ metocean_service/
COPY ais_service/ ais_service/
COPY scripts/ scripts/
COPY pytest.ini .env.example ./

COPY docker/backend-entrypoint.sh /usr/local/bin/backend-entrypoint.sh
RUN chmod +x /usr/local/bin/backend-entrypoint.sh scripts/get_weights.sh

# data/ is expected as a bind mount at runtime; create it so a bare
# `docker run` without the mount still boots.
RUN mkdir -p data/runs

EXPOSE 8000

# Liveness only — /health probes the process, not the upstream providers.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# backend.main bootstraps sys.path itself; it just needs to be started from
# main_system/ so `backend.*` resolves.
WORKDIR /app/main_system
ENTRYPOINT ["/usr/local/bin/backend-entrypoint.sh"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
