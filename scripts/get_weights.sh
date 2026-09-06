#!/bin/sh
# OceanTrace — fetch model weights (screen.onnx + segment.onnx) into the path
# the /detect service reads. POSIX sh (runs in the python:slim container and
# on Git Bash / macOS / Linux).
#
#   sh scripts/get_weights.sh
#
# Weights are NEVER committed (.gitignore: *.onnx) and never baked into the
# Docker image; they are pulled from a GitHub Release. Base URL comes from
# OILGUARD_WEIGHTS_URL, e.g.
#   https://github.com/INDHUPRIYAN/AIS-SAR-OilSpill-Vessel-Attribution/releases/download/weights-v1
#
# Exit codes: 0 = weights present and verified, 1 = missing/failed.

set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(dirname -- "$SCRIPT_DIR")
WEIGHTS_DIR="$REPO_ROOT/main_system/backend/services/detection/weights"

DEFAULT_URL="https://github.com/INDHUPRIYAN/AIS-SAR-OilSpill-Vessel-Attribution/releases/download/weights-v1"
BASE_URL="${OILGUARD_WEIGHTS_URL:-$DEFAULT_URL}"
BASE_URL="${BASE_URL%/}"

# name  sha256  size-in-bytes — computed from the artefacts exported by
# `python -m ml.export` / `python -m ml.train_yolo --export`.
# Update BOTH values when re-exporting.
WEIGHTS="screen.onnx 9a6ff8df0d2b4a8f8a287895c6780030c336c5c7f61f1d16d11cc89ceb9ac9fd 10423456
segment.onnx a4aac81f3ddd54ce05859c0fc4faada7b196288ed64a54293ab96488c5804a44 97719644"

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        python3 -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$1"
    fi
}

size_of() {
    # wc -c is POSIX and avoids stat's GNU/BSD flag split.
    wc -c < "$1" | tr -d '[:space:]'
}

weight_ok() {
    # $1=path $2=sha256 $3=size
    [ -f "$1" ] || return 1
    [ "$(size_of "$1")" = "$3" ] || return 1
    [ "$(sha256_of "$1")" = "$2" ] || return 1
    return 0
}

mkdir -p "$WEIGHTS_DIR"

FAILED=""
# Iterate via positional parameters (not a pipe) so FAILED survives the loop.
set -- $WEIGHTS
while [ "$#" -ge 3 ]; do
    NAME=$1; SHA=$2; SIZE=$3; shift 3
    TARGET="$WEIGHTS_DIR/$NAME"

    if weight_ok "$TARGET" "$SHA" "$SIZE"; then
        echo "[get_weights] $NAME already present, hash OK - skipping."
        continue
    fi
    [ -f "$TARGET" ] && echo "[get_weights] $NAME exists but size/hash mismatch - re-downloading."

    URL="$BASE_URL/$NAME"
    TMP="$TARGET.download"
    echo "[get_weights] downloading $URL ..."
    if curl -fSL --retry 3 -o "$TMP" "$URL"; then
        mv -f "$TMP" "$TARGET"
        if weight_ok "$TARGET" "$SHA" "$SIZE"; then
            echo "[get_weights] $NAME downloaded and sha256 verified."
        else
            echo "[get_weights] ERROR: $NAME failed sha256/size verification - deleting."
            rm -f "$TARGET"
            FAILED="$FAILED $NAME"
        fi
    else
        echo "[get_weights] ERROR downloading $NAME"
        rm -f "$TMP"
        FAILED="$FAILED $NAME"
    fi
done

if [ -z "$FAILED" ]; then
    echo "[get_weights] all weights present and verified in $WEIGHTS_DIR"
    exit 0
fi

echo ""
echo "[get_weights] FAILED for:$FAILED"
if [ "$BASE_URL" = "$DEFAULT_URL" ]; then
    cat <<'EOF'

The GitHub Release 'weights-v1' probably does not exist yet. Someone with the
trained weights (they live only on the training laptop today, at
  main_system/backend/services/detection/weights/screen.onnx
  main_system/backend/services/detection/weights/segment.onnx
) must publish them once:

  cd <repo root>
  gh release create weights-v1 \
      main_system/backend/services/detection/weights/screen.onnx \
      main_system/backend/services/detection/weights/segment.onnx \
      --title "Model weights v1" --notes "screen.onnx (YOLO11n DARTIS) + segment.onnx (U-Net Trujillo)"

Then re-run this script. To pull from a different host instead, set:
  export OILGUARD_WEIGHTS_URL="https://<host>/<path>"   (files served as <url>/screen.onnx etc.)
EOF
fi
echo "[get_weights] NOTE: without weights the /detect service still runs, using the threshold_fallback engine."
exit 1
