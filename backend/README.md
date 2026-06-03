<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# CellScope — Backend

FastAPI backend for **CellScope**, a self-hostable single-cell RNA-seq browser.
It loads an AnnData (`.h5ad`) file (lazily/backed for large files), serves the
embedding and per-cell color data over a compact little-endian binary protocol,
and runs marker / re-clustering / UMAP recompute jobs on cell selections.

The REST + WebSocket API, binary layouts, headers, and shared DTOs are specified
in [`../docs/CONTRACT.md`](../docs/CONTRACT.md) — that document is authoritative.

## Requirements

- Python **3.11+**
- The dependencies in [`requirements.txt`](./requirements.txt) (also restated in
  [`pyproject.toml`](./pyproject.toml)).

## Development setup

```bash
# from the backend/ directory
python -m venv .venv

# activate the virtualenv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\Activate.ps1     # Windows PowerShell

pip install -r requirements.txt
# (optional) editable install with dev extras for tests/lint:
# pip install -e ".[dev]"
```

## Run the dev server

```bash
uvicorn app.main:app --reload
```

The API is served under the `/api` prefix; check liveness at
`GET http://localhost:8000/api/health` → `{"status": "ok", "version": "0.1.0"}`.

In development the Vite frontend runs separately on `:5173`; set
`CELLSCOPE_CORS_ORIGINS` accordingly (the default `*` already permits it). For a
single-image production build the frontend is built to static assets and served
by this app via `StaticFiles` (set `CELLSCOPE_STATIC_DIR`).

## Configuration

All configuration is read from `CELLSCOPE_*` environment variables by
[`app/config.py`](./app/config.py). The full table of variables, defaults, and
meanings is documented in **section 3 of [`../docs/CONTRACT.md`](../docs/CONTRACT.md)**
(host, port, data dir, backed threshold, upload cap, marker rest cap, selection
LRU, static dir, CORS origins, autoload).

A common quick start that auto-loads a dataset on startup:

```bash
export CELLSCOPE_DATA_DIR=./data
export CELLSCOPE_AUTOLOAD=./data/pbmc3k.h5ad
uvicorn app.main:app --reload
```

## Tests

```bash
pytest          # tests live under tests/ (see [tool.pytest.ini_options])
```

Tests use synthetic, in-memory AnnData fixtures and never download data.

## Lint

```bash
ruff check .    # line-length 100; rule sets E, F, I, UP, B
```

## Layout

```
app/
├── main.py               # FastAPI app factory + static mount + SPA fallback
├── config.py             # Settings (pydantic-settings)
├── models.py             # pydantic request/response schemas (CONTRACT §6)
├── serialization.py      # binary encoders/decoders (Float32/Int32)
├── jobs.py               # async job manager + result store
├── services/             # lazy/backed AnnData I/O + compute
└── routers/              # datasets, color, selection, jobs API routers
```

## License

GPL-3.0-or-later. See the repository [`LICENSE`](../LICENSE).
