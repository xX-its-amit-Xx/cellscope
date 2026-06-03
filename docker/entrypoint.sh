#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
#
# CellScope container entrypoint (POSIX sh — works under dash/busybox).
#
# Responsibilities:
#   1. Ensure the data directory exists.
#   2. If no .h5ad is present in CELLSCOPE_DATA_DIR, generate a small synthetic
#      sample dataset (no network, fast/small) via examples/generate_sample.py.
#   3. Point CELLSCOPE_AUTOLOAD at a dataset so the API loads it on startup.
#   4. exec uvicorn from the backend dir so `app.main:app` imports correctly.
#
# This file must be executable. The Dockerfile runs `chmod +x` on it; if you run
# it directly from a checkout, do: chmod +x docker/entrypoint.sh

set -eu

# Defaults mirror CONTRACT section 3 (also baked into the image env).
: "${CELLSCOPE_DATA_DIR:=/data}"
: "${CELLSCOPE_PORT:=8000}"
APP_DIR="${CELLSCOPE_APP_DIR:-/app}"

SAMPLE_PATH="${CELLSCOPE_DATA_DIR}/sample.h5ad"

mkdir -p "${CELLSCOPE_DATA_DIR}"

# Find the first existing .h5ad in the data dir (top level). `set --` captures
# the glob expansion into positional params; an unmatched glob stays literal,
# so we test for actual file existence.
existing=""
for f in "${CELLSCOPE_DATA_DIR}"/*.h5ad; do
    if [ -f "$f" ]; then
        existing="$f"
        break
    fi
done

if [ -n "$existing" ]; then
    echo "[entrypoint] Found existing dataset: ${existing}"
else
    echo "[entrypoint] No .h5ad found in ${CELLSCOPE_DATA_DIR}; generating sample dataset..."
    # generate_sample.py accepts --out and otherwise defaults to
    # \$CELLSCOPE_DATA_DIR/sample.h5ad (see examples/generate_sample.py).
    python "${APP_DIR}/examples/generate_sample.py" --out "${SAMPLE_PATH}"
    existing="${SAMPLE_PATH}"
    echo "[entrypoint] Generated sample dataset: ${existing}"
fi

# Auto-load the dataset on startup unless the operator already set one.
if [ -z "${CELLSCOPE_AUTOLOAD:-}" ]; then
    CELLSCOPE_AUTOLOAD="${existing}"
fi
export CELLSCOPE_AUTOLOAD
echo "[entrypoint] CELLSCOPE_AUTOLOAD=${CELLSCOPE_AUTOLOAD}"

# Run from the backend dir so the `app` package resolves for `app.main:app`.
cd "${APP_DIR}/backend"

echo "[entrypoint] Starting uvicorn on 0.0.0.0:${CELLSCOPE_PORT}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${CELLSCOPE_PORT}"
