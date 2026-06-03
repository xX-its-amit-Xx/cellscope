<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# 01 — PBMC 3k Quickstart

**Dataset:** 10x Genomics PBMC 3k — ~2,700 peripheral blood mononuclear cells
from a healthy donor, the canonical scanpy tutorial dataset.
**Time:** ~5 minutes (download is a few MB).
**You will:** prepare a real `.h5ad`, load it, color by four marker genes, and
box-select a cluster to read its top markers.

> Outputs below (cell counts, marker tables, the ASCII figure) are
> **illustrative**. See the [cookbook README](./README.md) for why exact numbers
> vary.

---

## 1. Prepare the data

CellScope ships `examples/download_pbmc3k.py`. By **default** (`--source
processed`) it fetches scanpy's pre-processed PBMC3k object
(`sc.datasets.pbmc3k_processed()`, ~2,638 cells already filtered, normalized,
and log-transformed), reuses its existing `louvain` annotation as `leiden`,
ensures an `X_umap` embedding exists (computing one only if missing), and writes
`data/pbmc3k.h5ad`. This is the fastest, most reproducible option.

```bash
# from the repo root, in your scanpy prep environment
python examples/download_pbmc3k.py
```

**Expected output:**

```text
cellscope.download_pbmc3k: Downloading scanpy.datasets.pbmc3k_processed() ...
cellscope.download_pbmc3k: Computing UMAP            # skipped when X_umap already present (usually the case)
cellscope.download_pbmc3k: Reusing existing 'louvain' annotation as 'leiden'
cellscope.download_pbmc3k: Wrote data/pbmc3k.h5ad (~28 MB)
cellscope.download_pbmc3k:   shape:     2638 cells x 1838 genes
cellscope.download_pbmc3k:   obsm keys: X_pca, X_umap
cellscope.download_pbmc3k:   leiden:    8 clusters
```

The processed object is already QC-filtered to ~2,638 cells over ~1,838 genes,
matching the long-standing scanpy PBMC3k tutorial. If the object already ships an
`X_umap`, the `Computing UMAP` step is skipped; the exact counts can drift by a
few between scanpy versions.

### Run the full pipeline from raw counts (`--source raw`)

If you want the canonical raw → filtered pipeline instead of the pre-processed
object, pass `--source raw`. This fetches the raw count matrix
(`sc.datasets.pbmc3k()`, 2,700 cells × 32,738 genes) and runs the standard
preprocessing — filter → normalize → log1p → HVG → scale → PCA → neighbors →
UMAP → leiden — down to ~2,638 cells × ~1,838 highly-variable genes:

```bash
python examples/download_pbmc3k.py --source raw
```

**Expected output:**

```text
cellscope.download_pbmc3k: Downloading scanpy.datasets.pbmc3k() ...
cellscope.download_pbmc3k: Filtering cells/genes
cellscope.download_pbmc3k: Normalizing + log1p
cellscope.download_pbmc3k: Selecting highly variable genes
cellscope.download_pbmc3k: PCA -> neighbours -> UMAP -> Leiden
cellscope.download_pbmc3k: Wrote data/pbmc3k.h5ad (~28 MB)
cellscope.download_pbmc3k:   shape:     2638 cells x 1838 genes
cellscope.download_pbmc3k:   obsm keys: X_pca, X_umap
cellscope.download_pbmc3k:   leiden:    8 clusters
```

The numbers (2,700 raw cells → ~2,638 after QC filtering; ~1,838 highly-variable
genes kept) match the long-standing scanpy PBMC3k tutorial. The cell count can
drift by a few if scanpy's default QC thresholds change between versions.

If you'd rather do the prep yourself, here's the canonical scanpy PBMC3k
tutorial. (The bundled `--source raw` path is a streamlined variant — it filters
on mitochondrial % rather than `n_genes_by_counts` — but lands at the same
~2,638-cell ballpark.)

```python
# SPDX-License-Identifier: GPL-3.0-or-later
import scanpy as sc

adata = sc.datasets.pbmc3k()                      # 2700 x 32738
sc.pp.filter_cells(adata, min_genes=200)
sc.pp.filter_genes(adata, min_cells=3)
adata.var["mt"] = adata.var_names.str.startswith("MT-")
sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], inplace=True)
adata = adata[adata.obs.n_genes_by_counts < 2500]
adata = adata[adata.obs.pct_counts_mt < 5].copy()

sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)                                # X is now log-normalized
adata.raw = adata
sc.pp.highly_variable_genes(adata, min_mean=0.0125, max_mean=3, min_disp=0.5)
adata = adata[:, adata.var.highly_variable].copy()
sc.pp.scale(adata, max_value=10)

sc.tl.pca(adata, svd_solver="arpack")             # -> obsm["X_pca"]
sc.pp.neighbors(adata, n_neighbors=10, n_pcs=40)
sc.tl.umap(adata)                                 # -> obsm["X_umap"]
sc.tl.leiden(adata, resolution=1.0)               # -> obs["leiden"] (categorical)

adata.write_h5ad("data/pbmc3k.h5ad")
```

> [!NOTE]
> After `sc.pp.scale`, `adata.X` holds **scaled** values (z-scored, clipped) of
> only the highly-variable genes. The pipeline above stashes the full
> log-normalized matrix in `adata.raw`, but **CellScope colors from `adata.X`**,
> not `.raw`. For nicer gene-expression coloring (non-negative, interpretable),
> prefer writing the **log-normalized** matrix into `X` and skipping the scale
> step before `write_h5ad`, or write a `log1p` layer and color by that layer.
> See the "raw vs normalized X" gotcha in
> [recipe 04](./04-interop-scanpy-seurat-cellranger.md). The bundled
> `examples/download_pbmc3k.py` keeps `X` log-normalized for exactly this
> reason.

---

## 2. Load it in CellScope

With the server running (see [README prerequisites](./README.md#prerequisites)),
there are two ways in.

**Drag-and-drop.** Open <http://localhost:8000>, then drag `data/pbmc3k.h5ad`
onto the **File Loader** drop zone. (This is the upload path — the file is sent
to the server via `POST /api/datasets/upload`, saved under the data dir, and
loaded.)

**Load by path.** Since the file already lives in the server's data dir, type
the path in the File Loader's "Load by path" field and submit:

```text
pbmc3k.h5ad
```

A relative name resolves against `CELLSCOPE_DATA_DIR`; an absolute path also
works. This drives `POST /api/datasets/load`.

**Verify from the CLI** (handy for scripting / sanity checks):

```bash
curl -s -X POST http://localhost:8000/api/datasets/load \
  -H 'Content-Type: application/json' \
  -d '{"path": "pbmc3k.h5ad"}' | python -m json.tool
```

**Expected `DatasetInfo`:**

```json
{
    "dataset_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
    "path": "/data/pbmc3k.h5ad",
    "n_obs": 2638,
    "n_vars": 1838,
    "backed": false,
    "embeddings": ["X_pca", "X_umap"],
    "default_embedding": "X_umap",
    "obs_columns": [
        {"name": "n_genes", "kind": "continuous", "min": 200.0, "max": 2499.0},
        {"name": "percent_mito", "kind": "continuous", "min": 0.0, "max": 0.049},
        {"name": "n_counts", "kind": "continuous", "min": 556.0, "max": 15843.0},
        {"name": "leiden", "kind": "categorical", "n_categories": 8,
         "categories": ["0", "1", "2", "3", "4", "5", "6", "7"]}
    ],
    "var_index_name": "gene_symbols",
    "n_genes": 1838
}
```

`backed` is `false`: at ~28 MB the file is well under the default 500 MB
threshold, so it's read fully into memory. `default_embedding` is `X_umap`.

---

## 3. See the embedding

The viewport requests the embedding as a binary Float32 buffer (interleaved
`x,y`) and renders it with deck.gl. You should immediately see the familiar
PBMC3k UMAP: a few well-separated blobs. Color is uniform until you pick
something in **Color By**.

**Expected UMAP layout** (orientation is arbitrary — UMAP has no canonical
rotation/flip, so yours may be mirrored):

```text
            UMAP of PBMC3k  (~2,638 cells)

                  .:::.            .o8888o.
               .:::::::::.        o88888888888o      <- Monocytes
              ::::::::::::::     o8888888888888o        (CD14+ / FCGR3A+)
               ':::::::::'        '88888888888'
                    |                   .
                    |  (sparse bridge)  .
        .ooooooo.   |               .x x x.
     .ooooooooooooo.            .x x x x x x x.       <- T cells
    ooooooooooooooooo          x x x x x x x x x         (CD3D+, the big island)
     'ooooooooooooo'            'x x x x x x x'
        '#######'                  'x x x x'
        ########  <- B cells          .**.
        '######'     (MS4A1+)        .****.   <- NK
                                      '**'    (NKG7+)

       legend: o monocytes   x T cells   # B cells   * NK / other
```

The point is qualitative: PBMCs resolve into a **large T-cell territory**, a
**monocyte** cloud, a compact **B-cell** group, and a small **NK** group, plus
minor populations (dendritic cells, platelets). The exact placement of islands
is not meaningful; their *separation* is.

---

## 4. Color by marker genes

Open **Color By**, search a gene, and click the hit. CellScope fetches
`GET /api/datasets/{id}/expression?gene=<GENE>` (Float32 per cell) and applies a
continuous colormap (default `viridis`). Min/max come back in the response
headers, so the legend is auto-scaled.

Do these four in turn:

| Gene | Marks | Expected pattern on the UMAP |
|------|-------|------------------------------|
| **LYZ** | Monocytes (myeloid) | Bright over the monocyte cloud; dark elsewhere. |
| **CD3D** | T cells | Bright over the large T-cell island; dark on monocytes/B. |
| **MS4A1** | B cells | A small, sharply bright B-cell group; dark elsewhere. |
| **NKG7** | NK cells (+ cytotoxic T) | Bright on the small NK group, some spill into CD8 T. |

```bash
# CLI sanity check: fetch CD3D expression, read the headers
curl -s -D - -o /dev/null \
  'http://localhost:8000/api/datasets/<DATASET_ID>/expression?gene=CD3D'
```

**Expected response headers:**

```text
HTTP/1.1 200 OK
content-type: application/octet-stream
x-cellscope-dtype: float32
x-cellscope-n-obs: 2638
x-cellscope-gene: CD3D
x-cellscope-min: 0.0
x-cellscope-max: 4.81
```

The body is `2638 * 4 = 10552` bytes. (`X-Cellscope-Min/Max` reflect the
log-normalized range in this file; with a counts matrix you'd see larger
integers.)

> [!TIP]
> Gene search is case-insensitive substring matching. Typing `cd3` returns
> `CD3D`, `CD3E`, `CD3G`, etc.; typing `lyz` returns `LYZ`.

The four panels together should read like a textbook: **LYZ** and **CD3D** light
up mutually exclusive territories (myeloid vs lymphoid), **MS4A1** isolates a
tight B-cell knot, and **NKG7** picks out the small NK group adjacent to the
cytotoxic edge of the T-cell island.

---

## 5. Identify and box-select a cluster

Switch **Color By** to the categorical `leiden` column (a discrete legend with 8
colors appears — these come from `obs_columns[*].categories`, so the legend
labels are stable even though *which* number is monocytes varies run-to-run).

Use the **box-select** tool in the toolbar and drag a rectangle around the
**monocyte** cloud (the region that was brightest for **LYZ**). On mouse-up:

1. The frontend collects the indices of points inside the box (point-in-box in
   data space) into an `Int32Array`.
2. It registers them via `POST /api/datasets/{id}/selection` with an
   `application/octet-stream` body (raw Int32 indices). The server replies with
   a `SelectionRef`:

   ```json
   { "selection_id": "f0e1d2c3...", "n_cells": 480 }
   ```

3. The **Selection Panel** shows the count (`~480 cells selected`).

---

## 6. View top markers for the selection

In the **Selection Panel**, click **Compute stats / markers**. This posts to
`POST /api/datasets/{id}/selection/stats` (using the `selection_id` from the
previous step). The server runs scanpy `rank_genes_groups` with the `wilcoxon`
method, comparing your selection against a random sample of the rest.

```bash
curl -s -X POST \
  http://localhost:8000/api/datasets/<DATASET_ID>/selection/stats \
  -H 'Content-Type: application/json' \
  -d '{"selection_id": "f0e1d2c3...", "n_markers": 10}' \
  | python -m json.tool
```

**Expected `SelectionStatsResponse` (monocyte box):**

```json
{
    "n_cells": 480,
    "fraction": 0.182,
    "n_markers": 10,
    "rest_cells_used": 2158,
    "markers": [
        {"name": "LYZ",    "score": 38.1, "log2fc": 4.6, "pval": 1.0e-300, "pval_adj": 1.0e-298, "pct_in": 0.99, "pct_out": 0.34},
        {"name": "S100A9", "score": 36.4, "log2fc": 5.1, "pval": 1.0e-300, "pval_adj": 1.0e-298, "pct_in": 0.98, "pct_out": 0.21},
        {"name": "S100A8", "score": 35.0, "log2fc": 5.4, "pval": 1.0e-300, "pval_adj": 1.0e-298, "pct_in": 0.97, "pct_out": 0.18},
        {"name": "FCN1",   "score": 31.2, "log2fc": 4.2, "pval": 1.0e-280, "pval_adj": 1.0e-277, "pct_in": 0.95, "pct_out": 0.09},
        {"name": "CST3",   "score": 29.8, "log2fc": 3.1, "pval": 1.0e-260, "pval_adj": 1.0e-257, "pct_in": 0.96, "pct_out": 0.22},
        {"name": "TYROBP", "score": 28.5, "log2fc": 3.4, "pval": 1.0e-255, "pval_adj": 1.0e-252, "pct_in": 0.94, "pct_out": 0.17}
    ],
    "obs_summary": {
        "leiden": {"kind": "categorical", "counts": {"0": 471, "3": 7, "5": 2}, "top": "0"},
        "n_counts": {"kind": "continuous", "mean": 3850.2, "median": 3604.0, "min": 922.0, "max": 15843.0, "std": 1840.5}
    },
    "notes": ["rest sampled to 2158 of 2158 cells (under cap 50000)"]
}
```

`LYZ`, `S100A9`, `S100A8`, `FCN1` at the top — unambiguous **monocyte/myeloid**
markers. The `obs_summary.leiden.top` confirms the box landed mostly in one
Leiden cluster.

### Cross-check the other lineages

Box-select each of the other groups and recompute. Expected top markers:

| Box around… | Expected top markers | Identity |
|-------------|----------------------|----------|
| Monocyte cloud (LYZ-bright) | LYZ, S100A9, S100A8, FCN1, CST3 | CD14+ monocytes |
| Large T-cell island | **CD3D**, CD3E, **IL7R**, LDHB, TRAC | T cells (IL7R skews CD4/naïve) |
| Compact B-cell knot | **MS4A1**, **CD79A**, CD79B, HLA-DRA, CD74 | B cells |
| Small NK group | **NKG7**, GNLY, GZMB, PRF1, KLRD1 | NK cells |

This is the core CellScope loop: **color → spot a population → select → markers
→ identity**. Recipe 02 scales it up and adds on-the-fly sub-clustering.

---

## Troubleshooting

- **Viewport is empty / "no embedding".** The file has no `X_*` obsm key. Make
  sure `sc.tl.umap` (or t-SNE/PCA) ran before `write_h5ad`. Check `embeddings`
  in the `DatasetInfo`.
- **`leiden` shows as continuous (a gradient, not discrete colors).** The column
  is numeric, not categorical. Fix in prep:
  `adata.obs["leiden"] = adata.obs["leiden"].astype("category")`.
- **Gene not found (404).** Confirm the symbol exists in `var_names`. The
  PBMC3k var index uses **gene symbols** (`gene_symbols`), so `CD3D` works; if a
  file is indexed by Ensembl IDs, search the Ensembl ID instead (or set
  `var_names` to symbols, with `var_names_make_unique()` — see recipe 04).
- **Expression looks washed out / all bright.** `X` is probably raw counts or
  z-scored. Color by a log-normalized layer instead, or re-prep with
  log-normalized `X`.
