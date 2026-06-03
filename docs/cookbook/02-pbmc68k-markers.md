<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# 02 — PBMC 68k: markers and sub-clustering at mid scale

**Dataset:** 10x Genomics "Fresh 68k PBMCs (Donor A)" — ~68,000 peripheral
blood mononuclear cells, the classic mid-size 10x benchmark.
**Time:** download a few hundred MB; prep is a handful of minutes on a laptop;
the browser part is interactive.
**You will:** prepare a larger `.h5ad`, load it (watch **backed mode** engage),
color by marker genes, **lasso-select** the T-cell territory, run
`rank_genes_groups` from the Selection Panel, and recompute a **Leiden
sub-clustering** on just that selection over the WebSocket — surfacing CD4 vs
CD8 structure that the global clustering glosses over.

> Outputs below (counts, marker tables, timings, the ASCII figure) are
> **illustrative**. See the [cookbook README](./README.md) for why exact numbers
> vary. Leiden cluster *numbers* are not stable across runs — identify
> sub-clusters by their markers (CD4/CCR7 vs CD8A/GZMK), not by integer label.

If you have not read it yet, do [recipe 01](./01-pbmc3k-quickstart.md) first —
it covers the load → color → select → markers loop on a tiny dataset. This
recipe assumes that loop and focuses on **scale** and **recompute**.

---

## 1. Get the data

The 68k PBMC dataset is public from 10x Genomics. There is no scanpy
`sc.datasets` shortcut for it (unlike PBMC 3k), so download the matrix directly.
The canonical distribution is the filtered gene–barcode matrix tarball:

```bash
mkdir -p data/raw_68k && cd data/raw_68k

# 10x Genomics public "Fresh 68k PBMCs (Donor A)" filtered matrices (legacy mm9/hg19 layout)
curl -L -O https://cf.10xgenomics.com/samples/cell-exp/1.1.0/fresh_68k_pbmc_donor_a/fresh_68k_pbmc_donor_a_filtered_gene_bc_matrices.tar.gz

tar -xzf fresh_68k_pbmc_donor_a_filtered_gene_bc_matrices.tar.gz
# -> filtered_matrices_mex/hg19/{matrix.mtx,genes.tsv,barcodes.tsv}
cd ../..
```

**Expected layout after extraction:**

```text
data/raw_68k/filtered_matrices_mex/hg19/
├── barcodes.tsv      # ~68,579 barcodes
├── genes.tsv         # 32,738 genes (Ensembl ID  <TAB>  symbol)
└── matrix.mtx        # sparse counts (MatrixMarket)
```

> [!NOTE]
> 10x has reorganized download URLs over the years; if the link 404s, search
> "10x Genomics 68k PBMC donor A" on support.10xgenomics.com and grab the
> *filtered* gene–barcode matrices for the **hg19** reference. Any equivalent
> mirror works — you only need the `matrix.mtx` + `genes.tsv` + `barcodes.tsv`
> triple (or a single `filtered_feature_bc_matrix.h5`). This is a legacy
> Cell Ranger 1.x layout, so the per-gene file is `genes.tsv`, not the newer
> `features.tsv.gz`; `sc.read_10x_mtx` handles both.

---

## 2. Prepare a browsable `.h5ad`

A documented prep snippet — read the matrix, run the standard scanpy pipeline,
and write a file with an `X_umap` embedding plus a global `leiden` clustering.
Save it as `prep_pbmc68k.py` and run it in your scanpy prep environment.

```python
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare the 10x PBMC 68k dataset as a CellScope-browsable .h5ad."""

import logging
import scanpy as sc

logging.basicConfig(level=logging.INFO)

# 1. Read the 10x MatrixMarket directory (legacy layout: genes.tsv).
#    var_names_default="gene_symbols" indexes by symbol so CD3D/CD8A resolve;
#    var_names_make_unique() de-duplicates the (many) repeated symbols.
adata = sc.read_10x_mtx(
    "data/raw_68k/filtered_matrices_mex/hg19",
    var_names="gene_symbols",
    cache=True,
)
adata.var_names_make_unique()                       # CRITICAL: symbols repeat
logging.info("raw: %d cells x %d genes", adata.n_obs, adata.n_vars)

# 2. Light QC (68k is already filtered to called cells; this trims debris).
sc.pp.filter_cells(adata, min_genes=200)
sc.pp.filter_genes(adata, min_cells=3)
adata.var["mt"] = adata.var_names.str.startswith("MT-")
sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], percent_top=None,
                           log1p=False, inplace=True)
adata = adata[adata.obs.pct_counts_mt < 10].copy()

# 3. Normalize + log1p, then KEEP X log-normalized (CellScope colors from X).
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)                                  # X is now log-normalized
adata.raw = adata                                   # stash full log-norm matrix

# 4. HVG on a COPY for the embedding; do not overwrite X's gene set.
hvg = adata.copy()
sc.pp.highly_variable_genes(hvg, n_top_genes=2000)
hvg = hvg[:, hvg.var.highly_variable].copy()
sc.pp.scale(hvg, max_value=10)
sc.tl.pca(hvg, n_comps=50, svd_solver="arpack")
sc.pp.neighbors(hvg, n_neighbors=15, n_pcs=40)
sc.tl.umap(hvg)
sc.tl.leiden(hvg, resolution=1.0)                   # global clustering

# 5. Carry the embedding + clustering back onto the log-normalized object so
#    gene coloring reads interpretable values while the scatter uses the HVG UMAP.
adata.obsm["X_pca"] = hvg.obsm["X_pca"]
adata.obsm["X_umap"] = hvg.obsm["X_umap"]
adata.obs["leiden"] = hvg.obs["leiden"].values      # already categorical

# 6. Write. Uncompressed h5ad is larger on disk but maps faster in backed mode.
adata.write_h5ad("data/pbmc68k.h5ad")
logging.info("wrote data/pbmc68k.h5ad (%d x %d)", adata.n_obs, adata.n_vars)
```

```bash
python prep_pbmc68k.py
```

**Expected output:**

```text
INFO:root:raw: 68579 cells x 32738 genes
... reading from cache file cache/...-hg19-matrix.h5ad
INFO:root:wrote data/pbmc68k.h5ad (68551 x 32738)
```

The file lands around **0.7–1.5 GB** depending on compression. Two things to
notice for what follows:

- We keep **all 32,738 genes** in `X` (log-normalized), so any marker is
  colorable; the embedding is computed from a 2,000-HVG copy. This is the
  pattern CellScope likes: rich `X` for coloring + a clean `X_umap` for layout.
- Because the file is **≥ 500 MB** (`CELLSCOPE_BACKED_THRESHOLD_MB`, CONTRACT
  §3), CellScope will open it in **backed mode** — `obs`/`var`/`obsm` in RAM,
  `X` left on disk and read one gene-column at a time.

> [!TIP]
> Want backed mode to engage on a smaller file (to feel it on a laptop)? Lower
> the threshold: `CELLSCOPE_BACKED_THRESHOLD_MB=200 docker compose up`. Want to
> force in-memory for speed on a big-RAM box? Raise it above the file size.

---

## 3. Load it — backed mode engages

Put `pbmc68k.h5ad` in the server's data dir (it already is, under `data/`), then
**Load by path** in the File Loader:

```text
pbmc68k.h5ad
```

Or from the CLI:

```bash
curl -s -X POST http://localhost:8000/api/datasets/load \
  -H 'Content-Type: application/json' \
  -d '{"path": "pbmc68k.h5ad"}' | python -m json.tool
```

**Expected `DatasetInfo`:**

```json
{
    "dataset_id": "7f3c9a1b2d4e6f8a0c2e4d6f8a1b3c5d",
    "path": "/data/pbmc68k.h5ad",
    "n_obs": 68551,
    "n_vars": 32738,
    "backed": true,
    "embeddings": ["X_pca", "X_umap"],
    "default_embedding": "X_umap",
    "obs_columns": [
        {"name": "n_genes_by_counts", "kind": "continuous", "min": 200.0, "max": 4892.0},
        {"name": "total_counts", "kind": "continuous", "min": 556.0, "max": 39204.0},
        {"name": "pct_counts_mt", "kind": "continuous", "min": 0.0, "max": 9.98},
        {"name": "leiden", "kind": "categorical", "n_categories": 13,
         "categories": ["0","1","2","3","4","5","6","7","8","9","10","11","12"]}
    ],
    "var_index_name": "gene_symbols",
    "n_genes": 32738
}
```

The key field is **`"backed": true`**. Loading is fast and cheap (only `obs` /
`var` / `obsm` are read into RAM — the contract guarantees embeddings and
metadata stay cheap in backed mode, CONTRACT §2). The full `X` never touches
RAM at load time.

**Expected timing (qualitative):**

| Step | In-memory (`backed:false`) | Backed (`backed:true`) |
|------|----------------------------|------------------------|
| `load` (metadata + obsm) | a few seconds (reads all of `X`) | **sub-second** (skips `X`) |
| `embedding` fetch (68k Float32 xy) | instant | instant (obsm in RAM) |
| `expression` fetch (one gene) | instant (RAM) | **one disk seek + read**, tens of ms |

---

## 4. See the embedding and color by markers

The viewport requests `GET /api/datasets/{id}/embedding?key=X_umap` — a Float32
buffer of length `2 * 68551`, interleaved `x,y` (CONTRACT §4.5). ~68k points is
nothing for deck.gl's instanced renderer; it stays at 60fps with no
downsampling.

**Expected UMAP layout** (orientation arbitrary — UMAP has no canonical flip):

```text
        UMAP of PBMC 68k  (~68,500 cells)

                        .ooooooooo.
                     .ooooooooooooooo.       <- Monocytes / DC
                    ooooooooooooooooooo         (LYZ, CD14, FCGR3A)
                     'ooooooooooooooo'
                            | |
       xxxxxxxxxxxxx        | |        .*****.
     xxxxxxxxxxxxxxxxx     (T cells)  .********.   <- NK
    xxxxxxxxxxxxxxxxxxx  <-- one big  .********.      (NKG7, GNLY)
     xxxxxxxxxxxxxxxxx      island      '****'
       xxxxxxxxxxxxx      (CD3D+)
            |  the T-cell island looks like ONE blob at r=1.0 global Leiden;
            |  sub-clustering (step 7) splits it into CD4 vs CD8.
         #######
        #########  <- B cells
         #######      (MS4A1, CD79A)

      legend: o myeloid   x T cells   # B cells   * NK
```

Color by these in **Color By** (search the gene, click the hit). Each fetches
`GET /api/datasets/{id}/expression?gene=<GENE>`:

| Gene | Marks | Expected pattern |
|------|-------|------------------|
| **CD3D** | All T cells | Bright over the entire big T-cell island. |
| **CD4** | CD4 helper T | Bright over **one side** of the T island. |
| **CD8A** | CD8 cytotoxic T | Bright over the **other side** of the T island. |
| **CCR7** | Naïve T (CD4/CD8) | Bright on the naïve-leaning edge. |
| **LYZ** | Monocytes | Bright over the myeloid cloud. |
| **NKG7** | NK (+ cytotoxic) | Bright on the NK group, spill into CD8 edge. |
| **MS4A1** | B cells | Tight bright B-cell knot. |

The biological hook: **CD4 and CD8A paint complementary halves of the single
T-cell island.** Global Leiden at resolution 1.0 often lumps these into one or
two clusters; we will split them apart in step 7.

```bash
# Sanity-check one gene's response headers (backed mode does a disk read here):
curl -s -D - -o /dev/null \
  'http://localhost:8000/api/datasets/<DATASET_ID>/expression?gene=CD8A'
```

**Expected response headers:**

```text
HTTP/1.1 200 OK
content-type: application/octet-stream
x-cellscope-dtype: float32
x-cellscope-n-obs: 68551
x-cellscope-gene: CD8A
x-cellscope-min: 0.0
x-cellscope-max: 5.12
```

Body is `68551 * 4 = 274204` bytes. The client asserts
`byteLength / 4 == X-Cellscope-N-Obs` (CONTRACT §9.2) before uploading to the
GPU.

---

## 5. Lasso-select the T-cell population

Pick the **lasso** tool from the toolbar and draw a freehand loop around the big
**CD3D-bright** island (lasso beats box here — the T territory is an irregular
shape). On mouse-up:

1. The frontend runs point-in-polygon in data space (`lib/selection.ts`) and
   collects the inside indices into an `Int32Array`.
2. It registers them with `POST /api/datasets/{id}/selection`, body
   `application/octet-stream` = raw **Int32** indices (CONTRACT §4.7 — the
   binary form scales to millions). Response is a `SelectionRef`:

   ```json
   { "selection_id": "c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9", "n_cells": 41020 }
   ```

3. The **Selection Panel** shows `~41,020 cells selected`.

> [!TIP]
> Lasso a generous loop — the markers and sub-clustering are robust to a few
> stray monocytes/NK cells caught at the boundary, and you will *see* them as a
> small contaminating sub-cluster after step 7.

---

## 6. Markers for the selection (`rank_genes_groups`)

In the **Selection Panel**, click **Compute stats / markers**. This posts to
`POST /api/datasets/{id}/selection/stats` with the `selection_id`. The server
runs scanpy `rank_genes_groups` (method `wilcoxon`, CONTRACT §4.7) comparing
your ~41k-cell selection against a **random sample of the rest**, capped at
`CELLSCOPE_MARKER_REST_CAP` (default 50,000). With ~27.5k "rest" cells here, no
capping happens.

```bash
curl -s -X POST \
  http://localhost:8000/api/datasets/<DATASET_ID>/selection/stats \
  -H 'Content-Type: application/json' \
  -d '{"selection_id": "c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9", "n_markers": 10}' \
  | python -m json.tool
```

**Expected `SelectionStatsResponse` (T-cell lasso):**

```json
{
    "n_cells": 41020,
    "fraction": 0.5984,
    "n_markers": 10,
    "rest_cells_used": 27531,
    "markers": [
        {"name": "CD3D",  "score": 142.3, "log2fc": 3.9, "pval": 1.0e-300, "pval_adj": 1.0e-298, "pct_in": 0.97, "pct_out": 0.11},
        {"name": "CD3E",  "score": 138.7, "log2fc": 3.6, "pval": 1.0e-300, "pval_adj": 1.0e-298, "pct_in": 0.95, "pct_out": 0.10},
        {"name": "TRAC",  "score": 131.0, "log2fc": 3.4, "pval": 1.0e-300, "pval_adj": 1.0e-298, "pct_in": 0.94, "pct_out": 0.13},
        {"name": "IL7R",  "score": 118.2, "log2fc": 3.1, "pval": 1.0e-300, "pval_adj": 1.0e-298, "pct_in": 0.81, "pct_out": 0.14},
        {"name": "LTB",   "score": 104.5, "log2fc": 2.4, "pval": 1.0e-290, "pval_adj": 1.0e-287, "pct_in": 0.88, "pct_out": 0.29},
        {"name": "LDHB",  "score":  98.1, "log2fc": 1.9, "pval": 1.0e-280, "pval_adj": 1.0e-277, "pct_in": 0.90, "pct_out": 0.41}
    ],
    "obs_summary": {
        "leiden": {"kind": "categorical",
                   "counts": {"0": 16880, "1": 12940, "4": 8120, "6": 2300, "2": 780},
                   "top": "0"},
        "total_counts": {"kind": "continuous", "mean": 2810.4, "median": 2604.0,
                         "min": 556.0, "max": 21344.0, "std": 1190.7}
    },
    "notes": ["rest sampled to 27531 of 27531 cells (under cap 50000)"]
}
```

`CD3D`, `CD3E`, `TRAC`, `IL7R` at the top — unambiguous **pan-T-cell** markers.
The `obs_summary.leiden.counts` shows the lasso spans **several** global Leiden
clusters (0, 1, 4, …) — exactly the lumping we will resolve next. Note that
markers here are computed against the *rest of the dataset*, so they describe
"T cells vs everything else", not CD4 vs CD8 — the sub-clustering does that.

> [!NOTE]
> **Timing (qualitative).** In backed mode, markers require loading
> `adata[selection]` plus the sampled rest into memory and running the Wilcoxon
> test (CONTRACT §8 strategy). For ~41k + ~27k cells over 32k genes that is on
> the order of **a few to tens of seconds** on a laptop — disk-read + scanpy
> bound, not network. The response's `rest_cells_used` and `notes` report the
> actual sampling so you can trust the comparison.

---

## 7. Recompute a Leiden sub-clustering on the selection (WebSocket)

This is the headline feature: re-cluster **only the selected cells**, on the
fly, to expose structure the global clustering hid.

Open the **Recompute Panel**, choose **Leiden (recluster)**, set a resolution
(start at `1.0`), and submit. The client opens a WebSocket to
`/api/ws/jobs` and sends a `JobSubmit` (CONTRACT §5):

```json
{
  "action": "submit",
  "job_type": "recluster",
  "dataset_id": "<DATASET_ID>",
  "selection_id": "c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9",
  "params": { "resolution": 1.0 }
}
```

**Expected server → client frames** (progress is pushed as the worker advances;
`step` values are the coarse labels from CONTRACT §5 — clients tolerate unknown
ones):

```json
{ "type": "accepted",  "job_id": "9a8b7c6d", "job_type": "recluster" }
{ "type": "progress",  "job_id": "9a8b7c6d", "step": "subset",    "progress": 0.10, "message": "Subsetting 41020 cells to memory" }
{ "type": "progress",  "job_id": "9a8b7c6d", "step": "pca",       "progress": 0.35, "message": "PCA" }
{ "type": "progress",  "job_id": "9a8b7c6d", "step": "neighbors", "progress": 0.60, "message": "Building kNN graph" }
{ "type": "progress",  "job_id": "9a8b7c6d", "step": "leiden",    "progress": 0.90, "message": "Leiden clustering" }
{ "type": "completed", "job_id": "9a8b7c6d", "job_type": "recluster",
  "result_url": "/api/jobs/9a8b7c6d/result",
  "summary": { "n_clusters": 6, "n_cells": 41020 } }
```

Per the backed-mode strategy (CONTRACT §8), the server subsets the selection
`.to_memory()`, reuses the existing `X_pca` rows if present (it is — we wrote
`X_pca`), then `sc.pp.neighbors` + `sc.tl.leiden` (flavor `igraph`, falling back
to the default).

The client then downloads the labels:

```bash
curl -s -D - -o /tmp/recluster.bin \
  http://localhost:8000/api/jobs/9a8b7c6d/result
```

**Expected result headers** (CONTRACT §4.8):

```text
HTTP/1.1 200 OK
content-type: application/octet-stream
x-cellscope-job-type: recluster
x-cellscope-n: 41020
x-cellscope-n-clusters: 6
x-cellscope-dtype: int32
```

Body is `41020 * 4 = 164080` bytes of **Int32** cluster labels, **in selection
order** (label `k` for the `k`-th selected cell). The store keeps these as
`reclusterLabels` and applies them as an *ephemeral categorical coloring* over
just the selection (CONTRACT §7), so the T-island repaints into discrete
sub-clusters while the rest of the embedding stays as-is.

### Expected: CD4 vs CD8 sub-structure

The single T-cell island now resolves into sub-clusters. Color each
sub-cluster's territory by `CD4`, `CD8A`, and `CCR7` (or lasso a sub-cluster and
compute its markers) to read identities:

| Sub-cluster (by markers, **not** number) | Top markers | Identity |
|-------------------------------------------|-------------|----------|
| `IL7R`, `CCR7`, `CD4`, `LEF1` | naïve/memory CD4 | **CD4 T (naïve/central memory)** |
| `CD8A`, `CD8B`, `CCL5`, `GZMK` | cytotoxic | **CD8 T (effector/memory)** |
| `CCR7`, `SELL`, `LEF1`, `TCF7` high; `CD8A` low | quiescent | **Naïve T** |
| `NKG7`, `GNLY`, `GZMB` (and `CD3D` low) | contamination | **NK caught in the lasso** |

That last row is the boundary contamination from step 5 made visible — a useful
sanity check that the recompute is honest.

> [!NOTE]
> **Timing (qualitative).** Reclustering ~41k cells is dominated by the kNN
> graph build and Leiden iterations — typically **seconds to a couple of
> minutes** on a laptop. Reusing the pre-computed `X_pca` (rather than running
> PCA from scratch) is the big saver; that is exactly why the prep script in
> step 2 wrote `X_pca`. Raise `resolution` to split further (more, smaller
> sub-clusters); lower it to merge.

### Optionally: recompute a UMAP on the selection

Same panel, choose **Recompute UMAP** instead. The `JobSubmit` uses
`job_type: "recompute_umap"` with `params: { "n_neighbors": 15, "min_dist": 0.3 }`
(CONTRACT §5). The result is a Float32 buffer length `2 * n_selected`,
interleaved xy (CONTRACT §4.8), which overlays/replaces the selection's
coordinates (`recomputedPositions` in the store) — a fresh embedding of just the
T cells, where CD4/CD8 separate even more cleanly than in the global UMAP.

**Expected result headers:**

```text
x-cellscope-job-type: recompute_umap
x-cellscope-n: 41020
x-cellscope-bounds: -8.41,-7.92,9.03,8.55
x-cellscope-dtype: float32
```

---

## Troubleshooting

- **File didn't open in backed mode (`"backed": false`).** It is under the
  threshold. Either it compressed below 500 MB, or your build set a higher
  `CELLSCOPE_BACKED_THRESHOLD_MB`. Write uncompressed, or lower the threshold —
  see the tip in step 2.
- **Gene 404 for `CD8A` / `CD3D`.** The file is indexed by Ensembl IDs, not
  symbols. Re-run the prep with `var_names="gene_symbols"` (step 2) and
  `var_names_make_unique()`, or search by Ensembl ID. See the var-names gotcha
  in [recipe 04](./04-interop-scanpy-seurat-cellranger.md).
- **`leiden` colors as a gradient.** It's numeric, not categorical. Fix in prep:
  `adata.obs["leiden"] = adata.obs["leiden"].astype("category")`.
- **Recompute errored: "selection too small to cluster".** A handful of cells
  cannot form a kNN graph. Select a larger region (hundreds+).
- **Markers/recompute feel slow.** That's the disk-read + scanpy cost in backed
  mode, not the network. See [`../performance.md`](../performance.md) for the
  backed-mode marker/recompute strategy and how the `MARKER_REST_CAP` sampling
  bounds the work.

Next: [recipe 03](./03-atlas-scale.md) takes this to **1M+ cells**, where backed
mode and the **downsampling toggle** stop being optional.
