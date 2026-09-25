#!/bin/sh
# OceanTrace single-container entrypoint: data, then weights, then the server.
# Neither step is fatal. The API always boots and reports what is missing
# rather than crash-looping where nobody can read why.
set -u

echo "[entrypoint] data bundle..."
python /app/scripts/deploy/fetch_data.py \
    || echo "[entrypoint] WARNING: data bundle not fetched; serving what DATA_ROOT holds."

echo "[entrypoint] model weights..."
sh /app/scripts/get_weights.sh \
    || echo "[entrypoint] WARNING: weights missing; /detect fails honestly until they are installed."

exec "$@"
