#!/bin/sh
# OceanTrace backend entrypoint: pull model weights at START (never baked into
# the image), then exec the server. A failed download is logged but NOT fatal —
# the /detect service falls back to threshold+morphology without weights.
set -u

echo "[entrypoint] fetching model weights (skipped if already present)..."
if sh /app/scripts/get_weights.sh; then
    echo "[entrypoint] weights ready."
else
    echo "[entrypoint] WARNING: weight download failed; /detect will use the" \
         "threshold_fallback engine. Set OILGUARD_WEIGHTS_URL to fix."
fi

exec "$@"
