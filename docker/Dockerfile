# syntax=docker/dockerfile:1
# SPDX-License-Identifier: GPL-3.0-or-later
#
# CellScope — single image, single port (8000).
#
# Multi-stage build:
#   1. "frontend" stage builds the React/TypeScript/deck.gl app to static assets.
#   2. final stage installs the FastAPI backend and copies the built assets in,
#      so one container serves both the API (under /api) and the frontend
#      (FastAPI StaticFiles), per CONTRACT section 1.
#
# Build context MUST be the repository root (see docker-compose.yml), because we
# copy both `frontend/` and `backend/` (and `examples/`).

# ---------------------------------------------------------------------------
# Stage 1 — build the frontend to /frontend/dist
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend

WORKDIR /frontend

# Copy manifests first so the dependency install layer is cached independently
# of source changes. The package-lock.json* glob is optional: it is copied when
# present but is not required by `npm install`.
#
# We use `npm install` (not `npm ci`) because no frontend/package-lock.json is
# committed yet, and `npm ci` hard-fails without a lockfile. ROADMAP: commit a
# frontend/package-lock.json and switch this back to `npm ci` for fully
# reproducible builds (the preferred setup).
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

# Copy the rest of the frontend sources and produce the production build.
# `npm run build` runs `tsc && vite build` and emits to /frontend/dist
# (see frontend/vite.config.ts `build.outDir`).
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 — backend runtime image
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS final

# Heavy-deps caveat:
#   scanpy/anndata pull in numpy/scipy/pandas; clustering uses leidenalg +
#   python-igraph. These ship manylinux wheels for CPython 3.11 on x86_64, so a
#   normal `pip install` needs NO compiler. We therefore do NOT install
#   build-essential by default (it would add hundreds of MB and slow builds).
#   If you build on an architecture without prebuilt igraph/leidenalg wheels
#   (e.g. some arm/musl targets), uncomment the apt-get block below so pip can
#   compile them from source.
#
# RUN apt-get update \
#  && apt-get install -y --no-install-recommends build-essential \
#  && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CELLSCOPE_STATIC_DIR=/app/static \
    CELLSCOPE_DATA_DIR=/data \
    CELLSCOPE_PORT=8000

WORKDIR /app

# Install Python dependencies first (cached unless requirements.txt changes).
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy the backend source and the sample-data generator/examples.
COPY backend/ ./backend/
COPY examples/ ./examples/

# Copy the built frontend assets from the frontend stage into the static dir
# that CELLSCOPE_STATIC_DIR points at.
COPY --from=frontend /frontend/dist/ ./static/

# Entrypoint script (POSIX sh). It auto-generates a sample dataset on first run
# and execs uvicorn from the backend dir so `app.main:app` imports correctly.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Run as a non-root user. Create the data dir up front and hand ownership of the
# app + data dirs to the unprivileged user so the entrypoint can write the
# generated sample.h5ad even when /data is an anonymous (unbound) volume.
RUN useradd --create-home --uid 10001 cellscope \
 && mkdir -p /data \
 && chown -R cellscope:cellscope /app /data
USER cellscope

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
