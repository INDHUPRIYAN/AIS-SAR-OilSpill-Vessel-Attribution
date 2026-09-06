#!/bin/sh
# OceanTrace — one-command demo (backup path; the demo laptop uses run_demo.ps1).
#
#   sh scripts/run_demo.sh              # local dev servers (uvicorn + vite)
#   sh scripts/run_demo.sh --docker     # docker compose stack
#
# Does, in order:
#   1. verify/fetch model weights (scripts/get_weights.sh)
#   2. verify demo run artefacts exist under data/runs
#   3. start backend (uvicorn :8000) + frontend (vite :5173), or compose
#   4. print the URL to open

set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(dirname -- "$SCRIPT_DIR")
USE_DOCKER=0
[ "${1:-}" = "--docker" ] && USE_DOCKER=1

echo "=== OceanTrace demo ==="

# --- 1. weights -------------------------------------------------------------
if ! sh "$SCRIPT_DIR/get_weights.sh"; then
    echo "[run_demo] WARNING: weights unavailable; /detect will use the threshold_fallback engine. Demo continues."
fi

# --- 2. demo run artefacts --------------------------------------------------
RUNS_DIR="$REPO_ROOT/data/runs"
RUN_COUNT=0
if [ -d "$RUNS_DIR" ]; then
    RUN_COUNT=$(find "$RUNS_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d '[:space:]')
fi
if [ "$RUN_COUNT" -gt 0 ]; then
    echo "[run_demo] $RUN_COUNT run(s) found under data/runs:"
    find "$RUNS_DIR" -mindepth 1 -maxdepth 1 -type d | sort | head -8 | sed 's|.*/|  - |'
else
    cat <<EOF

[run_demo] WARNING: data/runs is EMPTY - the dashboard will have nothing to show.
Seed at least one run before demoing. Either:
  a) copy the data/runs/ folder from the demo laptop into this clone, or
  b) generate one from the committed mock scene:
       cd "$REPO_ROOT/main_system"
       ../.venv/Scripts/python -m backend.services.pipeline.run \\
           --scene ../contracts/mocks/scene_sigma0_db.tif \\
           --scene-meta ../contracts/mocks/scene_meta.json --run-id inv-001
     (on Linux/macOS the venv python is ../.venv/bin/python)
Continuing anyway (the API itself works without runs).

EOF
fi

# --- 3. start services ------------------------------------------------------
if [ "$USE_DOCKER" -eq 1 ]; then
    if ! command -v docker >/dev/null 2>&1; then
        echo "[run_demo] ERROR: --docker requested but docker is not installed/on PATH."
        exit 1
    fi
    echo "[run_demo] starting docker compose stack (backend + frontend)..."
    (cd "$REPO_ROOT" && docker compose up --build -d) || {
        echo "[run_demo] ERROR: docker compose up failed."; exit 1; }
else
    # Prefer the repo venv; fall back to python3 on PATH.
    PY="$REPO_ROOT/.venv/bin/python"
    [ -x "$PY" ] || PY="$REPO_ROOT/.venv/Scripts/python.exe"   # Git Bash on Windows
    if [ ! -x "$PY" ]; then
        echo "[run_demo] NOTE: .venv not found; using python3 from PATH."
        echo "          (setup: python3 -m venv .venv && .venv/bin/python -m pip install -e .)"
        PY=python3
    fi

    echo "[run_demo] starting backend (uvicorn, port 8000)..."
    (cd "$REPO_ROOT/main_system" && "$PY" -m uvicorn backend.main:app --port 8000 \
        > "$REPO_ROOT/data/backend-demo.log" 2>&1 &) || exit 1

    FRONTEND_DIR="$REPO_ROOT/main_system/frontend"
    if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
        echo "[run_demo] node_modules missing - running npm install (one-time)..."
        (cd "$FRONTEND_DIR" && npm install) || {
            echo "[run_demo] ERROR: npm install failed. Is Node.js installed?"; exit 1; }
    fi
    echo "[run_demo] starting frontend (vite, port 5173)..."
    (cd "$FRONTEND_DIR" && npm run dev > "$REPO_ROOT/data/frontend-demo.log" 2>&1 &)
fi

# --- 4. wait for the backend, then print the URL ----------------------------
echo "[run_demo] waiting for backend http://127.0.0.1:8000/health ..."
HEALTHY=0
i=0
while [ $i -lt 45 ]; do
    if curl -fsS -m 2 http://127.0.0.1:8000/health >/dev/null 2>&1; then
        HEALTHY=1; break
    fi
    sleep 2
    i=$((i + 1))
done

echo ""
if [ "$HEALTHY" -eq 1 ]; then
    echo "=== OceanTrace is up ==="
else
    echo "=== Backend did not answer /health within 90 s - check data/backend-demo.log ==="
fi
echo "  Dashboard : http://localhost:5173"
echo "  API       : http://localhost:8000  (docs at /docs, liveness at /health)"
if [ "$USE_DOCKER" -eq 1 ]; then
    echo "  Stop with : docker compose down"
else
    echo "  Stop with : kill the uvicorn and vite processes (logs in data/*-demo.log)"
fi
