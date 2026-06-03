<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# 04 — Interop: Scanpy, Seurat, and Cell Ranger

**Goal:** point CellScope at the outputs of the tools you already use. CellScope
reads a standard AnnData `.h5ad`; the job here is getting *your* data into one
that satisfies the loader contract (CONTRACT §2):

1. **At least one `X_*` embedding** in `adata.obsm` (e.g. `X_umap`, `X_tsne`,
   `X_pca`). No embedding → the viewport has nothing to draw.
2. **Coloring follows the obs dtype** — a pandas **categorical** (or
   string/object) column colors as a **discrete legend**; a **numeric** column
   colors as a **continuous gradient**.

> Commands are real; printed numbers/results are **illustrative** (see the
> [cookbook README](./README.md)). Do [recipe 01](./01-pbmc3k-quickstart.md)
> once so the load → color → select loop is familiar.

This recipe is three independent sections — jump to the tool you use:
[**(a) Scanpy**](#a-scanpy--just-works) ·
[**(b) Seurat**](#b-seurat--convert-to-h5ad) ·
[**(c) Cell Ranger**](#c-cell-ranger--read-the-matrix-then-embed) — followed by
shared [**gotchas**](#shared-gotchas) that bite all three.

---

## (a) Scanpy → just works

Any `.h5ad` that has an `X_*` embedding in `obsm` and your metadata in `obs`
loads directly. If you already ran a scanpy pipeline, you're done — just
`write_h5ad` and load by path.

A minimal end-to-end pipeline, from a counts matrix to a CellScope-ready file:

```python
# SPDX-License-Identifier: GPL-3.0-or-later
import scanpy as sc

adata = sc.read_h5ad("my_counts.h5ad")     # or read_10x_mtx / read_csv / ...
adata.var_names_make_unique()              # safe even if already unique

# QC + normalize. KEEP X log-normalized so CellScope gene coloring is readable.
sc.pp.filter_cells(adata, min_genes=200)
sc.pp.filter_genes(adata, min_cells=3)
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)                         # X is now log-normalized
adata.raw = adata                          # optional: stash full log-norm matrix

# Embedding (CellScope's hard requirement: at least one X_* obsm key).
sc.pp.highly_variable_genes(adata, n_top_genes=2000)
sc.tl.pca(adata, n_comps=50)               # -> obsm["X_pca"]
sc.pp.neighbors(adata, n_neighbors=15, n_pcs=40)
sc.tl.umap(adata)                          # -> obsm["X_umap"]  (default_embedding)
sc.tl.leiden(adata, resolution=1.0)        # -> obs["leiden"]  (categorical)

# Make any label columns categorical so they color as legends, not gradients.
for col in ["leiden", "cell_type", "sample", "condition"]:
    if col in adata.obs:
        adata.obs[col] = adata.obs[col].astype("category")

adata.write_h5ad("data/my_scanpy.h5ad")
```

Load by path (`my_scanpy.h5ad`) exactly as in recipe 01.

**Expected `DatasetInfo` (abridged):**

```json
{
    "n_obs": 9421, "n_vars": 18742, "backed": false,
    "embeddings": ["X_pca", "X_umap"],
    "default_embedding": "X_umap",
    "obs_columns": [
        {"name": "leiden", "kind": "categorical", "n_categories": 11, "categories": ["0","1","..."]},
        {"name": "n_genes_by_counts", "kind": "continuous", "min": 201.0, "max": 6210.0}
    ],
    "n_genes": 18742
}
```

That's the whole story for scanpy: **if `obsm` has an `X_*` key, CellScope reads
it.** The default-embedding pick order is `X_umap` → `X_tsne` → `X_pca` → first
`X_*` (CONTRACT §2), so if you only ran PCA, CellScope still renders `X_pca` —
but compute a UMAP/t-SNE for an interpretable layout.

> [!TIP]
> Already have an embedding under a non-standard name? Rename it so the loader
> recognizes it: `adata.obsm["X_umap"] = adata.obsm.pop("umap")`. Only obsm keys
> starting with `X_` are offered as embeddings (CONTRACT §2).

---

## (b) Seurat → convert to `.h5ad`

CellScope does not read Seurat's `.rds`/`.h5Seurat` directly — convert to
AnnData `.h5ad` in R first. Two well-trodden routes; pick one.

### Route 1 — `sceasy` (one call)

[`sceasy`](https://github.com/cellgeek/sceasy) wraps the conversion and lands
Seurat reductions (`umap`, `pca`, `tsne`) into `adata.obsm` as `X_umap` /
`X_pca` / `X_tsne` — exactly what CellScope wants.

```r
# install.packages("remotes"); remotes::install_github("cellgeni/sceasy")
# Requires reticulate + a Python with anndata available.
library(sceasy)
library(Seurat)
library(reticulate)

# 'seurat_obj' is your processed Seurat object (already has a UMAP reduction).
sceasy::convertFormat(
  seurat_obj,
  from = "seurat",
  to   = "anndata",
  outFile = "data/from_seurat.h5ad",
  main_layer = "data",        # 'data' = log-normalized -> readable gene coloring
  assay = "RNA"
)
```

**Expected:** an `.h5ad` where Seurat's `reductions$umap` becomes
`adata.obsm["X_umap"]`, `reductions$pca` → `X_pca`, and the cell metadata
(`seurat_obj@meta.data`) becomes `adata.obs` — including your cluster column
(e.g. `seurat_clusters`). `main_layer = "data"` puts the **log-normalized**
matrix in `X` (recommended; see the raw-vs-normalized gotcha below).

### Route 2 — `SeuratDisk` (`SaveH5Seurat` + `Convert`)

```r
# remotes::install_github("mojaveazure/seurat-disk")
library(Seurat)
library(SeuratDisk)

SaveH5Seurat(seurat_obj, filename = "data/from_seurat.h5Seurat")
Convert("data/from_seurat.h5Seurat", dest = "h5ad")
# -> writes data/from_seurat.h5ad
```

**Expected:** `Convert` writes `from_seurat.h5ad`; reductions again land in
`obsm` as `X_umap` / `X_pca`. `SeuratDisk` defaults to writing the active assay's
normalized `data` slot to `X`.

### Verify and (if needed) fix the embedding name

Whichever route, confirm an `X_*` obsm key exists before loading:

```python
# SPDX-License-Identifier: GPL-3.0-or-later
import scanpy as sc
adata = sc.read_h5ad("data/from_seurat.h5ad")
print("obsm:", list(adata.obsm.keys()))
print("obs :", list(adata.obs.columns))
```

**Expected:**

```text
obsm: ['X_pca', 'X_umap']
obs : ['orig.ident', 'nCount_RNA', 'nFeature_RNA', 'seurat_clusters', 'cell_type', ...]
```

If the UMAP landed under a different key, rename it and (re)write:

```python
if "X_umap" not in adata.obsm and "umap" in adata.obsm:
    adata.obsm["X_umap"] = adata.obsm.pop("umap")
# Seurat cluster ids sometimes import as integers -> make categorical.
for col in ["seurat_clusters", "cell_type"]:
    if col in adata.obs:
        adata.obs[col] = adata.obs[col].astype("category")
adata.write_h5ad("data/from_seurat.h5ad")
```

Then load `from_seurat.h5ad` by path. Color by `seurat_clusters` (categorical
legend) and by your favorite marker gene.

> [!NOTE]
> **Seurat gotchas.** (1) Reduction key casing: Seurat stores `umap`/`pca`;
> conversion prefixes them to `X_umap`/`X_pca` — verify, don't assume.
> (2) `main_layer`/active assay decides what's in `X`. Use the log-normalized
> `data` layer, not `counts` (raw) or `scale.data` (z-scored), so gene coloring
> is interpretable. (3) `SeuratDisk` can choke on factor columns with odd
> levels; if `Convert` errors, drop or coerce the offending `meta.data` column
> in R first.

---

## (c) Cell Ranger → read the matrix, then embed

Cell Ranger emits a `filtered_feature_bc_matrix` (a directory of
`matrix.mtx.gz` + `features.tsv.gz` + `barcodes.tsv.gz`) and a bundled
`filtered_feature_bc_matrix.h5`. Either reads cleanly into scanpy — but **Cell
Ranger output has no embedding**, so you must run a pipeline to add an `X_*`
obsm key before CellScope can draw it.

### Read the matrix

```python
# SPDX-License-Identifier: GPL-3.0-or-later
import scanpy as sc

# Option A: the bundled HDF5 (single file, simplest).
adata = sc.read_10x_h5("outs/filtered_feature_bc_matrix.h5")

# Option B: the MTX directory (works for older Cell Ranger too).
# adata = sc.read_10x_mtx("outs/filtered_feature_bc_matrix",
#                         var_names="gene_symbols", cache=True)

adata.var_names_make_unique()      # CRITICAL: 10x gene symbols are NOT unique
```

`sc.read_10x_h5` indexes `var_names` by **gene symbol** by default and keeps the
Ensembl IDs in `adata.var["gene_ids"]`. Symbols repeat (e.g. multiple `TBCE`),
so `var_names_make_unique()` is **mandatory** — without it, gene search and
expression lookup are ambiguous.

### Run a standard pipeline to add an embedding

```python
# QC -> normalize -> log1p (keep X log-normalized) -> HVG -> PCA -> UMAP -> Leiden
sc.pp.filter_cells(adata, min_genes=200)
sc.pp.filter_genes(adata, min_cells=3)
adata.var["mt"] = adata.var_names.str.startswith("MT-")
sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], percent_top=None,
                           log1p=False, inplace=True)
adata = adata[adata.obs.pct_counts_mt < 15].copy()

sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)                 # X log-normalized -> readable gene coloring
adata.raw = adata

sc.pp.highly_variable_genes(adata, n_top_genes=2000)
sc.tl.pca(adata, n_comps=50)
sc.pp.neighbors(adata, n_neighbors=15, n_pcs=40)
sc.tl.umap(adata)                  # -> obsm["X_umap"]
sc.tl.leiden(adata, resolution=1.0)
adata.obs["leiden"] = adata.obs["leiden"].astype("category")

adata.write_h5ad("data/from_cellranger.h5ad")
```

```bash
python prep_cellranger.py
```

**Expected output:**

```text
... reading outs/filtered_feature_bc_matrix.h5
Variable names are not unique. To make them unique, call `.var_names_make_unique`.
... after QC: 7218 cells x 19544 genes
wrote data/from_cellranger.h5ad
```

(The "Variable names are not unique" warning is exactly why
`var_names_make_unique()` is in the snippet.)

Load `from_cellranger.h5ad` by path. Color by `leiden` (categorical) and by
marker genes — for a PBMC/immune sample, `CD3D`, `LYZ`, `MS4A1`, `NKG7` behave
just like recipe 01.

> [!NOTE]
> **Multi-sample / aggr.** `cellranger aggr` output reads the same way; the
> aggregated barcodes carry a `-N` suffix per sample. Add a sample column from
> that suffix and make it categorical to color by sample:
> `adata.obs["sample"] = adata.obs_names.str.split("-").str[-1].astype("category")`.
>
> **Feature Barcoding (CITE-seq / multiome).** `read_10x_h5` returns all feature
> types together; `adata.var["feature_types"]` distinguishes
> `Gene Expression` from `Antibody Capture` / `Peaks`. Subset to gene expression
> for the standard pipeline:
> `adata = adata[:, adata.var.feature_types == "Gene Expression"].copy()`.

---

## Shared gotchas

These bite regardless of source. They all trace back to the two loader rules at
the top.

### 1. `var_names_make_unique()` — always call it

10x/Cell Ranger gene symbols are **not** unique (Ensembl maps several IDs to the
same symbol). Duplicate `var_names` make gene search and the
`GET /…/expression?gene=` lookup ambiguous. Call `adata.var_names_make_unique()`
right after reading. It's a no-op if names are already unique, so it's always
safe.

### 2. Ensure an `X_*` obsm embedding exists

The single most common "it loaded but the viewport is empty" cause: **no
embedding.** The loader treats only `obsm` keys starting with `X_` as embeddings
and picks `X_umap` → `X_tsne` → `X_pca` → first `X_*` (CONTRACT §2). Check, and
rename if needed:

```python
print(list(adata.obsm.keys()))                 # must contain an X_* key
adata.obsm["X_umap"] = adata.obsm.pop("umap")   # if yours is named "umap"
```

Cell Ranger output has *no* embedding at all — you must compute one (section c).

### 3. Raw vs normalized `X` — color readability lives here

CellScope colors genes from **`adata.X`** (or a named `adata.layers[...]`), **not
`adata.raw`**. So what's in `X` decides whether expression coloring is readable:

| What's in `X` | How gene coloring looks | Recommendation |
|---------------|--------------------------|----------------|
| **Raw counts** | huge dynamic range; a few high cells wash out the rest | normalize + `log1p` before writing |
| **Log-normalized** (`normalize_total`+`log1p`) | non-negative, interpretable gradient | **preferred** — what every snippet here writes |
| **Scaled / z-scored** (`sc.pp.scale`) | centered at 0, negatives, clipped — confusing as color; only HVGs present | avoid in `X`; keep log-norm in `X` and scale only a copy used for PCA |

So: do `sc.pp.scale` on a **copy** used to compute PCA/UMAP, and keep the
**log-normalized** matrix in the `X` you write (recipes 02 and 03 do exactly
this). If you must keep scaled values in `X`, write the log-normalized matrix as
a layer and color by it:

```python
adata.layers["lognorm"] = adata.X.copy()   # before scaling
# ... sc.pp.scale(adata) ...                # X becomes scaled
adata.write_h5ad("data/with_layer.h5ad")
# In CellScope, the expression endpoint accepts ?layer=lognorm
```

CellScope's expression endpoint takes an optional `layer` (CONTRACT §4.6):
`GET /api/datasets/{id}/expression?gene=CD3D&layer=lognorm` reads
`adata.layers["lognorm"]` instead of `X`.

### 4. Make label columns categorical

A cluster/cell-type column stored as **int** or left as **object** may color as
a gradient or behave oddly. Cast the discrete ones to `category` so they get a
proper Int32-code + label legend (CONTRACT §4.6):

```python
for col in ["leiden", "seurat_clusters", "cell_type", "sample", "condition"]:
    if col in adata.obs:
        adata.obs[col] = adata.obs[col].astype("category")
```

Leave genuinely continuous columns (`total_counts`, `pct_counts_mt`, scores)
numeric — they're meant to be gradients.

### 5. Sparse `X` and dtype

CellScope reads `X` (and layers) and casts per-column to the wire dtype
(`float32`), so a `scipy` CSR/CSC sparse `X` is fine and preferred for big files
(smaller on disk, cheaper backed reads). You don't need to densify.

---

## Quick reference: what each tool gives you

| Source | Reader | Has embedding? | `X` default | Must do |
|--------|--------|----------------|-------------|---------|
| **Scanpy** `.h5ad` | `sc.read_h5ad` | if you ran `sc.tl.umap` | whatever you left | ensure `X_*` obsm; categorical labels |
| **Seurat** | `sceasy` / `SeuratDisk` | reductions → `obsm` | active assay slot (`data` recommended) | verify `X_umap` name; `main_layer="data"` |
| **Cell Ranger** | `sc.read_10x_h5` / `read_10x_mtx` | **no** | raw counts | `var_names_make_unique()`; compute a UMAP; log-normalize `X` |

With any of these, the in-browser experience is identical to recipe 01: load by
path → color by gene/metadata → select → markers → (optionally) recompute. For
scale and performance, see [recipe 03](./03-atlas-scale.md) and
[`../performance.md`](../performance.md).
