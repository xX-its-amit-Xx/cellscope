<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# CellScope Cookbook

End-to-end, runnable recipes for exploring **real** public single-cell RNA-seq
datasets in CellScope. Each recipe gives the actual CLI commands to fetch and
prepare the data, the exact UI steps to drive the browser, and the **expected**
biological/visual result.

> [!IMPORTANT]
> The commands in this cookbook are real and meant to be run on your own
> machine. The *outputs, marker tables, timings, and figures shown here are
> illustrative* — they were not executed inside the docs build. Real numbers
> depend on the dataset snapshot, your scanpy version, BLAS threading, and
> hardware. Treat printed counts, cluster IDs, gene rankings, and wall-clock
> timings as representative, not exact. Leiden cluster *numbers* in particular
> are not stable across runs/versions — identify clusters by their markers, not
> by their integer label.

---

## Recipes

| # | Recipe | Dataset | What you learn |
|---|--------|---------|----------------|
| 01 | [PBMC 3k quickstart](./01-pbmc3k-quickstart.md) | 10x PBMC 3k (~2,700 cells) | First load, color by marker genes, box-select a cluster, read its top markers. |
| 02 | [PBMC 68k markers](./02-pbmc68k-markers.md) | 10x PBMC 68k (~68,000 cells) | Backed mode, lasso selection, `rank_genes_groups`, Leiden sub-clustering over WebSocket. |
| 03 | [Atlas scale](./03-atlas-scale.md) | 1M+ cells (CELLxGENE Census / Tabula Sapiens / HLCA) | Large `.h5ad` via path, downsampling toggle, pan/zoom at scale, `cell_type` coloring, performance. |
| 04 | [Interop: Scanpy / Seurat / Cell Ranger](./04-interop-scanpy-seurat-cellranger.md) | Your own data | Pointing CellScope at outputs from existing tools. |

A good first-time path is **01 → 04** (learn the UI on the smallest dataset,
then bring your own data). Recipes 02 and 03 add scale and the recompute
features.

---

## Prerequisites

### 1. A running CellScope

Either of the following gives you a server on **http://localhost:8000**.

**Option A — Docker Compose (recommended for a quick look).** From the repo
root:

```bash
docker compose up --build
# ... build output ...
# Uvicorn running on http://0.0.0.0:8000
```

Open <http://localhost:8000>. The frontend is served by FastAPI `StaticFiles`;
the REST API lives under `/api` on the same port.

To make a host directory of `.h5ad` files visible to the container, mount it as
the data dir. In `docker-compose.yml` the service mounts a `data/` volume; drop
files there, or set the env var explicitly:

```bash
# example: serve a host folder of .h5ad files
CELLSCOPE_DATA_DIR=/data \
  docker compose up --build
# (mount your host folder to /data in docker-compose.yml)
```

**Option B — Local dev (recommended while iterating on data prep).** Two
terminals:

```bash
# Terminal 1 — backend (FastAPI on :8000)
cd backend
pip install -e .            # or: pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Terminal 2 — frontend (Vite dev server on :5173, proxies to :8000)
cd frontend
npm install
npm run dev
```

In dev, open the Vite URL (<http://localhost:5173>); it talks to the API on
:8000 (CORS is permissive by default — see `CELLSCOPE_CORS_ORIGINS`).

### 2. A data-prep environment (scanpy)

The download/prep scripts in these recipes use **scanpy**. A minimal, isolated
environment:

```bash
python -m venv .venv-prep
source .venv-prep/bin/activate        # Windows: .venv-prep\Scripts\activate
pip install "scanpy>=1.10" "anndata>=0.10" "leidenalg" "igraph"
```

`scanpy` pulls in `anndata`, `numpy`, `scipy`, and `pandas`. `leidenalg` +
`igraph` are only needed if a recipe asks you to compute Leiden clustering
locally during prep (CellScope itself runs Leiden server-side for the recompute
feature). Recipe 03 additionally uses `cellxgene-census`; recipe 04(b) uses R
packages (`sceasy`/`SeuratDisk`) — those are introduced where they are needed.

### 3. A data directory

CellScope scans `CELLSCOPE_DATA_DIR` (default `./data`) for `.h5ad` files and
lists them as `available_files`. Create it and put prepared files there:

```bash
mkdir -p data
```

---

## What CellScope needs from an `.h5ad` (read this once)

Every recipe produces a file that satisfies the loader contract. The two rules
that matter:

1. **At least one embedding.** The loader treats every `adata.obsm` key starting
   with `X_` (e.g. `X_umap`, `X_tsne`, `X_pca`) as an embedding and uses the
   first two columns for the scatter. If there is **no** `X_*` key, the dataset
   loads but the viewport has nothing to draw and the client shows an error.
   Default pick order is `X_umap` → `X_tsne` → `X_pca` → first `X_*`.
   *If your file only has `X_pca`, CellScope will still render it — but you
   usually want a UMAP/t-SNE for interpretation.*

2. **Coloring follows the obs dtype.** A `pandas` **categorical** column (or
   string/object column) in `adata.obs` becomes **categorical coloring** (Int32
   codes + a label list). A **numeric** column becomes **continuous** coloring
   (Float32 + min/max). So make cluster/cell-type columns categorical for
   discrete legends:

   ```python
   adata.obs["cell_type"] = adata.obs["cell_type"].astype("category")
   ```

Gene coloring reads a single expression column from `adata.X` (or a named
`adata.layers[...]`). Whether values look like raw counts or log-normalized
expression depends on **what is in `X`** — see the "raw vs normalized X" gotcha
in [recipe 04](./04-interop-scanpy-seurat-cellranger.md).

---

## Conventions used in these docs

- Shell blocks are what you type. Lines beginning with `# expected:` (or blocks
  fenced and labelled **Expected output**) are illustrative results, not
  executed here.
- "Color by `GENE`" means: open the **Color By** panel, search the gene, click
  the hit. "Box-select" / "lasso-select" refer to the selection tools in the
  toolbar.
- File paths passed to *Load by path* are resolved absolute, or relative to
  `CELLSCOPE_DATA_DIR`. The recipes assume your prepared files live in `data/`.
- For the performance discussion behind backed mode, downsampling, and the
  binary transfer protocol, see [`../performance.md`](../performance.md).
