# CellScope — System Contract (Single Source of Truth)

> This document is the **authoritative interface spec** for CellScope. Every backend
> and frontend module is built against it. If code and this document disagree, this
> document wins (open a PR to change the contract first). The README's "Binary
> Transfer Protocol" section is a human-friendly summary of §4 here.

CellScope is a self-hostable single-cell RNA-seq browser: load an AnnData (`.h5ad`)
file and interactively explore a UMAP/t-SNE embedding of up to ~5M cells at 60fps,
color by gene/metadata, lasso/box select, and recompute clustering/UMAP on a subset.

License: **GNU GPL v3.0-or-later**. Every source file carries an SPDX header:
- Python: `# SPDX-License-Identifier: GPL-3.0-or-later`
- TS/TSX/JS: `// SPDX-License-Identifier: GPL-3.0-or-later`

---

## 1. Repository layout (file ownership is fixed)

```
cellscope/
├── LICENSE                         # GPL-3.0 (already present)
├── README.md
├── .gitignore
├── docker-compose.yml
├── .github/workflows/ci.yml
├── backend/
│   ├── pyproject.toml
│   ├── requirements.txt
│   ├── README.md
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI app factory + static mount
│   │   ├── config.py               # Settings (pydantic-settings)
│   │   ├── models.py               # pydantic request/response schemas
│   │   ├── serialization.py        # binary encoders/decoders (Float32/Int32)
│   │   ├── jobs.py                 # async job manager + result store
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   └── anndata_service.py  # lazy/backed AnnData I/O + compute
│   │   └── routers/
│   │       ├── __init__.py
│   │       ├── datasets.py         # load + metadata + embedding
│   │       ├── color.py            # gene search, expression, obs columns
│   │       ├── selection.py        # selection register + stats (markers)
│   │       └── jobs.py             # WebSocket /ws/jobs + result download
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py             # synthetic tiny AnnData fixtures
│       ├── test_serialization.py
│       ├── test_anndata_service.py
│       └── test_api.py             # FastAPI TestClient
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── tsconfig.node.json
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   ├── index.html
│   ├── .eslintrc.cjs
│   └── src/
│       ├── main.tsx
│       ├── index.css
│       ├── App.tsx
│       ├── types.ts                # mirrors §6 (DTOs) + §4 (protocol)
│       ├── api/
│       │   ├── client.ts           # REST client (fetch + binary)
│       │   ├── binary.ts           # ArrayBuffer <-> typed array parsing
│       │   └── jobs.ts             # WebSocket job client
│       ├── store/
│       │   └── useStore.ts         # Zustand store (§7)
│       ├── lib/
│       │   ├── colormap.ts         # value -> RGB (continuous + categorical)
│       │   └── selection.ts        # box + point-in-polygon (data space)
│       ├── hooks/
│       │   └── useColorBuffer.ts   # builds Uint8Array(n*3) color buffer
│       └── components/
│           ├── EmbeddingViewport.tsx  # deck.gl ScatterplotLayer (binary attrs)
│           ├── Tooltip.tsx
│           ├── Toolbar.tsx
│           ├── FileLoader.tsx
│           ├── ColorByPanel.tsx
│           ├── SelectionPanel.tsx
│           └── RecomputePanel.tsx
├── docker/
│   ├── Dockerfile                  # multi-stage: build FE, install BE, one image
│   ├── nginx-not-used.md           # (we serve FE via FastAPI StaticFiles)
│   └── entrypoint.sh
├── docs/
│   ├── CONTRACT.md                 # this file
│   ├── architecture.md
│   ├── protocol.md                 # human summary of §4/§5
│   ├── performance.md
│   └── cookbook/
│       ├── README.md
│       ├── 01-pbmc3k-quickstart.md
│       ├── 02-pbmc68k-markers.md
│       ├── 03-atlas-scale.md
│       └── 04-interop-scanpy-seurat-cellranger.md
└── examples/
    ├── README.md
    ├── generate_sample.py          # synthetic .h5ad, no network, fast/small
    └── download_pbmc3k.py          # fetch real 10x PBMC3k via scanpy
```

Serving model: **single image, single port**. The frontend is built to static
assets and served by FastAPI `StaticFiles`; the API lives under `/api`. Default
port **8000**. `docker compose up` builds both and serves everything on
`http://localhost:8000`.

---

## 2. Data model & dataset lifecycle

- A loaded dataset gets a `dataset_id` (uuid4 hex). The server keeps a registry:
  `dataset_id -> LoadedDataset(adata, path, backed, info)`.
- AnnData is opened with `anndata.read_h5ad(path, backed="r")` when the file is
  at or above (≥) `CELLSCOPE_BACKED_THRESHOLD_MB` (default 500), else read fully into
  memory (`backed=None`). In backed mode, `obs`, `var`, and `obsm` are in RAM;
  only `X` (and layers) stay on disk — so embeddings and metadata are cheap, gene
  expression is the only disk-bound read.
- Embeddings are `adata.obsm` keys starting with `X_` (e.g. `X_umap`, `X_tsne`,
  `X_pca`). Only the first 2 columns are used for the scatter.
- `default_embedding`: prefer `X_umap`, then `X_tsne`, then `X_pca`, else the first
  `X_*` obsm key; if none, `None` (client shows an error).
- Genes: `adata.var_names`. Search is case-insensitive substring/prefix.
- Selections are registered server-side and referenced by `selection_id`
  (see §4.6). The registry is an LRU (default 64 entries).

---

## 3. Configuration (env vars, read by `config.py`)

| Env var | Default | Meaning |
|---|---|---|
| `CELLSCOPE_HOST` | `0.0.0.0` | bind host |
| `CELLSCOPE_PORT` | `8000` | bind port |
| `CELLSCOPE_DATA_DIR` | `./data` | dir scanned for `.h5ad` and where uploads land |
| `CELLSCOPE_BACKED_THRESHOLD_MB` | `500` | files ≥ this open in backed mode |
| `CELLSCOPE_MAX_UPLOAD_MB` | `5120` | reject larger uploads |
| `CELLSCOPE_MARKER_REST_CAP` | `50000` | max "rest" cells sampled for markers |
| `CELLSCOPE_SELECTION_LRU` | `64` | cached selections |
| `CELLSCOPE_STATIC_DIR` | `""` | path to built frontend; empty = API only |
| `CELLSCOPE_CORS_ORIGINS` | `*` | comma list for dev (vite at :5173) |
| `CELLSCOPE_AUTOLOAD` | `""` | optional `.h5ad` path auto-loaded at startup |

---

## 4. REST API (prefix `/api`)

All binary responses are `application/octet-stream`, **little-endian**, no padding.
All custom headers are prefixed `X-Cellscope-` except the short aliases noted.
Errors use HTTP status + JSON `{"detail": "..."}` (FastAPI default).

### 4.1 `POST /api/datasets/load` — load by server-side path
Request JSON: `{ "path": "<absolute or DATA_DIR-relative .h5ad path>" }`
Response 200: **`DatasetInfo`** (§6.1).
Errors: 400 (bad/missing path), 404 (not found), 415 (not an .h5ad), 500.

### 4.2 `POST /api/datasets/upload` — multipart upload
`multipart/form-data` field `file` (the `.h5ad`). Saved under `DATA_DIR`, then
loaded. Response 200: **`DatasetInfo`**. 413 if over `MAX_UPLOAD_MB`.

### 4.3 `GET /api/datasets` — list loaded + discoverable datasets
Response: `{ "loaded": [DatasetSummary...], "available_files": ["pbmc3k.h5ad", ...] }`
`available_files` = `.h5ad` files found in `DATA_DIR` (not necessarily loaded).

### 4.4 `GET /api/datasets/{dataset_id}` — metadata
Response: **`DatasetInfo`**. 404 if unknown.

### 4.5 `GET /api/datasets/{dataset_id}/embedding?key=X_umap`
Binary body: **Float32** length `2 * n_obs`, **interleaved** `[x0,y0,x1,y1,...]`.
Headers:
- `X-Cellscope-Dtype: float32`
- `X-Cellscope-Layout: interleaved-xy`
- `X-Cellscope-N-Obs: <int>`
- `X-Cellscope-Key: X_umap`
- `X-Cellscope-Bounds: minX,minY,maxX,maxY` (4 floats, comma-separated)
`key` defaults to the dataset's `default_embedding`. 404 unknown dataset/key.

### 4.6 Color sources

**Gene search** `GET /api/datasets/{id}/genes?query=cd3&limit=50`
Response JSON: `{ "hits": [GeneHit...], "total": <int> }` (§6.3). Empty `query`
returns the first `limit` genes.

**Expression** `GET /api/datasets/{id}/expression?gene=CD3D&layer=X`
- `gene` may be a var_name or integer var index (string).
- `layer` optional; `X` (default) = `adata.X`, else `adata.layers[layer]`.
Binary body: **Float32** length `n_obs`. Headers:
- `X-Cellscope-Dtype: float32`
- `X-Cellscope-N-Obs: <int>`
- `X-Cellscope-Gene: <resolved var_name>`
- `X-Cellscope-Min: <float>`, `X-Cellscope-Max: <float>`
404 if gene not found.

**Obs column** `GET /api/datasets/{id}/obs?column=leiden`
Returns one of two shapes (distinguished by `X-Cellscope-Kind`):
- **categorical** → binary **Int32** codes length `n_obs` (code `-1` = NaN/missing).
  Headers: `X-Cellscope-Kind: categorical`, `X-Cellscope-N-Categories: <int>`,
  `X-Cellscope-Dtype: int32`, `X-Cellscope-N-Obs: <int>`.
  Category labels come from `DatasetInfo.obs_columns[*].categories` (already sent at
  load); the body holds integer codes indexing that array.
- **continuous** → binary **Float32** length `n_obs` (`NaN` allowed for missing).
  Headers: `X-Cellscope-Kind: continuous`, `X-Cellscope-Dtype: float32`,
  `X-Cellscope-Min`, `X-Cellscope-Max`, `X-Cellscope-N-Obs`.

### 4.7 Selections

**Register** `POST /api/datasets/{id}/selection`
Two accepted request bodies:
- `application/octet-stream`: raw **Int32** array of cell indices (preferred; scales
  to millions). Indices must be in `[0, n_obs)`.
- `application/json`: `{ "indices": [int, ...] }`.
Response JSON: **`SelectionRef`** = `{ "selection_id": "...", "n_cells": <int> }`.
Out-of-range indices → 400.

**Stats / markers** `POST /api/datasets/{id}/selection/stats`
Request JSON: **`SelectionStatsRequest`** (§6.4): exactly one of `selection_id` or
`indices`; optional `n_markers` (default 25), `obs_keys` (which obs cols to summarize;
default = all categorical with ≤50 cats + a few continuous).
Response JSON: **`SelectionStatsResponse`** (§6.5). Markers computed with scanpy
`rank_genes_groups` (method `wilcoxon`) comparing the selection vs a random sample of
the rest (capped at `MARKER_REST_CAP`); response notes the cap actually applied.

### 4.8 Job result download (paired with §5 WebSocket)
`GET /api/jobs/{job_id}/result`
- `recluster` → binary **Int32** length `n_selected` (cluster label per selected
  cell, in selection order). Headers: `X-Cellscope-Job-Type: recluster`,
  `X-Cellscope-N: <n_selected>`, `X-Cellscope-N-Clusters: <int>`,
  `X-Cellscope-Dtype: int32`.
- `recompute_umap` → binary **Float32** length `2*n_selected`, interleaved xy.
  Headers: `X-Cellscope-Job-Type: recompute_umap`, `X-Cellscope-N: <n_selected>`,
  `X-Cellscope-Bounds: minX,minY,maxX,maxY`, `X-Cellscope-Dtype: float32`.
404 if job unknown or not finished.

### 4.9 `GET /api/health` → `{"status":"ok","version":"<ver>"}`

---

## 5. WebSocket API — `/api/ws/jobs`

JSON text frames both directions. Long compute runs in a worker thread; progress is
pushed as it advances.

**Client → server** (`JobSubmit`, §6.6):
```json
{ "action": "submit",
  "job_type": "recluster" | "recompute_umap",
  "dataset_id": "<id>",
  "selection_id": "<id>",            // from POST /selection (required)
  "params": { "resolution": 1.0 }    // recluster: {resolution, n_neighbors?, n_pcs?}
}                                     // recompute_umap: {n_neighbors, min_dist?, n_pcs?}
```
Client may also send `{ "action": "cancel", "job_id": "<id>" }`.

**Server → client** messages (all include `job_id`):
```json
{ "type": "accepted",  "job_id": "...", "job_type": "recluster" }
{ "type": "progress",  "job_id": "...", "step": "neighbors", "progress": 0.5,
                       "message": "Building kNN graph" }            // progress in [0,1]
{ "type": "completed", "job_id": "...", "job_type": "recluster",
                       "result_url": "/api/jobs/<id>/result",
                       "summary": { "n_clusters": 7, "n_cells": 12345 } }
{ "type": "error",     "job_id": "...", "error": "human-readable message" }
{ "type": "cancelled", "job_id": "..." }
```
Defined `step` values (coarse, for UI labels): `subset`, `pca`, `neighbors`,
`leiden`, `umap`, `finalize`. Clients must tolerate unknown steps.

---

## 6. Shared DTOs (pydantic ↔ TypeScript must match field names exactly)

### 6.1 `DatasetInfo`
```
dataset_id: str
path: str | None
n_obs: int
n_vars: int
backed: bool
embeddings: list[str]                # obsm X_* keys
default_embedding: str | None
obs_columns: list[ObsColumnInfo]
var_index_name: str | None           # adata.var.index.name
n_genes: int                         # == n_vars
```

### 6.2 `ObsColumnInfo`
```
name: str
kind: "categorical" | "continuous"
n_categories: int | None             # categorical only
categories: list[str] | None         # categorical only, ordered to match codes
min: float | None                    # continuous only
max: float | None                    # continuous only
```

### 6.3 `GeneHit`
```
name: str
index: int                           # var position
```

### 6.4 `SelectionStatsRequest`
```
selection_id: str | None = None
indices: list[int] | None = None     # used if selection_id absent
n_markers: int = 25
obs_keys: list[str] | None = None
```

### 6.5 `SelectionStatsResponse`
```
n_cells: int
fraction: float                      # n_cells / n_obs
n_markers: int
rest_cells_used: int                 # after MARKER_REST_CAP sampling
markers: list[MarkerGene]
obs_summary: dict[str, ObsColumnSummary]
notes: list[str]                     # honest caveats (e.g. "rest subsampled to 50000")
```
`MarkerGene`:
```
name: str
score: float
log2fc: float
pval: float
pval_adj: float
pct_in: float                        # fraction of selection expressing (>0)
pct_out: float
```
`ObsColumnSummary` (one of):
- categorical: `{ "kind":"categorical", "counts": {label: int, ...}, "top": label }`
- continuous: `{ "kind":"continuous", "mean": f, "median": f, "min": f, "max": f, "std": f }`

### 6.6 `JobSubmit` / `JobProgress` / `JobCompleted` / `JobError`
Mirror §5 exactly.

---

## 7. Frontend Zustand store shape (`useStore.ts`)

```
dataset: DatasetInfo | null
loading: boolean
error: string | null

embeddingKey: string | null
positions: Float32Array | null       // interleaved xy, length 2*n
bounds: [number,number,number,number] | null
nObs: number

// coloring
colorMode: { type: "none" }
         | { type: "gene"; gene: string }
         | { type: "obs"; column: string }
colorValues: Float32Array | null     // continuous values OR Int32-as-codes (see codes)
colorCodes: Int32Array | null        // categorical codes when obs categorical
colorKind: "none" | "continuous" | "categorical"
colorDomain: [number, number] | null
categories: string[] | null
colormapName: string                 // e.g. "viridis"

// selection (indices into the FULL dataset)
selection: Int32Array | null
selectionId: string | null
selectionStats: SelectionStatsResponse | null

// recompute overlay
job: { id: string; type: string; progress: number; step: string; status: "running"|"done"|"error" } | null
reclusterLabels: Int32Array | null   // applied as an ephemeral categorical coloring
recomputedPositions: Float32Array | null  // overlay/replace coords for the selection

// view
downsample: boolean
downsampleN: number                   // target rendered count when downsampling
hovered: { index: number } | null

// actions: loadByPath, uploadFile, setEmbedding, colorByGene, colorByObs,
//          clearColor, setSelection, fetchSelectionStats, submitRecluster,
//          submitUmap, setDownsample, ...
```

The color buffer (`Uint8Array` length `n*3`) consumed by deck.gl is derived from
`colorValues`/`colorCodes` + `colormapName` in `useColorBuffer.ts` (not stored).

---

## 8. Backend service layer interface (`anndata_service.py`)

A module-level singleton `service = AnnDataService()`. All heavy reads return numpy
arrays already cast to the wire dtype (`float32`/`int32`, C-contiguous).

```python
class AnnDataService:
    def load(self, path: str) -> DatasetInfo: ...
    def load_uploaded(self, filename: str, data: bytes) -> DatasetInfo: ...
    def get_info(self, dataset_id: str) -> DatasetInfo: ...           # KeyError -> 404
    def list_loaded(self) -> list[DatasetSummary]: ...
    def available_files(self) -> list[str]: ...

    def get_embedding(self, dataset_id: str, key: str | None
                      ) -> tuple[np.ndarray, tuple[float,float,float,float]]:
        """Return (float32 [n,2] C-contiguous, bounds). key None -> default."""

    def search_genes(self, dataset_id: str, query: str, limit: int) -> tuple[list[GeneHit], int]: ...

    def get_expression(self, dataset_id: str, gene: str, layer: str | None
                       ) -> tuple[np.ndarray, str, float, float]:
        """Return (float32 [n], resolved_name, vmin, vmax). Reads one column."""

    def get_obs(self, dataset_id: str, column: str) -> "ObsResult":
        """ObsResult: kind, codes(int32[n]) | values(float32[n]), categories, vmin, vmax."""

    def register_selection(self, dataset_id: str, indices: np.ndarray) -> tuple[str, int]: ...
    def resolve_selection(self, dataset_id: str, selection_id: str | None,
                          indices: list[int] | None) -> np.ndarray: ...  # int32

    def selection_stats(self, dataset_id: str, indices: np.ndarray,
                        n_markers: int, obs_keys: list[str] | None) -> SelectionStatsResponse: ...

    # Long-running; `progress` is Callable[[str, float, str], None] (step, frac, msg).
    def recluster(self, dataset_id: str, indices: np.ndarray, params: dict,
                  progress) -> tuple[np.ndarray, int]:   # (int32 labels [n], n_clusters)
    def recompute_umap(self, dataset_id: str, indices: np.ndarray, params: dict,
                       progress) -> tuple[np.ndarray, tuple]:  # (float32 [2n], bounds)
```

Backed-mode marker/recompute strategy (document in performance.md):
- Markers: load only `adata[selection]` and a random sample of the rest (≤
  `MARKER_REST_CAP`) `.to_memory()`, concat, label, `sc.tl.rank_genes_groups`.
- recluster/umap: subset to selection `.to_memory()`; use existing `X_pca` rows if
  present (subset obsm), else `sc.pp.pca`; then `sc.pp.neighbors` + `sc.tl.leiden`
  (flavor `igraph`, fallback default) / `sc.tl.umap`.

---

## 9. Binary protocol invariants (the heart of the perf story)

1. Coordinates and per-cell scalars travel as raw little-endian typed arrays, never
   JSON. JSON is reserved for schema/metadata/markers.
2. `n_obs` is always echoed in `X-Cellscope-N-Obs`; clients allocate exactly
   `byteLength/4` elements and assert it equals the header.
3. Float32 for coordinates & continuous values; Int32 for categorical codes & cell
   indices. No mixed-dtype buffers.
4. Embedding & recomputed-UMAP buffers are interleaved xy (`size:2` for deck.gl
   `getPosition`). Expression/obs/labels are flat per-cell (`size:1`).
5. deck.gl consumes these as **binary attributes**:
   `data = { length: n, attributes: { getPosition: {value: Float32Array, size:2},
   getFillColor: {value: Uint8Array, size:3} } }`. No per-row JS objects.

---

## 10. Conventions

- Python: 3.11 target, full type hints, Google-style docstrings, `ruff` clean,
  SPDX header. No print; use `logging`.
- TS: strict mode, no `any` in exported signatures, TSDoc on exported funcs, SPDX
  header. ESLint clean. Functional React + hooks.
- Tests: pytest, synthetic AnnData (a few hundred cells) built in `conftest.py`;
  never download data in tests. FastAPI `TestClient` for API; assert binary header
  values and `byteLength`.
- Agents must NOT run `npm install`, `pip install`, builds, downloads, or compute —
  write files only (host is RAM-constrained). CI is where builds run.
