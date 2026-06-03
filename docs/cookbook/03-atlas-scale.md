<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# 03 — Atlas scale: 1M+ cells

**Dataset:** an atlas-scale `.h5ad` — millions of cells — exported from
**CZ CELLxGENE Census**, or an equivalent **Tabula Sapiens** / **Human Lung Cell
Atlas (HLCA)** download.
**Time:** the **download/export is large and slow** (many GB; minutes to hours
depending on the slice and your link). The browser part is interactive once the
file is on disk.
**You will:** export a large `.h5ad`, point CellScope at it by **path** (never
upload), rely on **backed mode**, use the **downsampling toggle** to keep the
viewport at 60fps, pan/zoom across millions of cells, color by `cell_type`, and
read off the performance characteristics.

> [!IMPORTANT]
> Everything below is **expected/projected**. These are multi-GB downloads and
> were not run in the docs build. Counts, file sizes, and timings depend
> entirely on the slice you export, your disk, RAM, and link. Treat the numbers
> as order-of-magnitude. See the [cookbook README](./README.md).

Do [recipe 02](./02-pbmc68k-markers.md) first — it introduces backed mode and
the selection/recompute loop on a tractable dataset. This recipe is about the
**operational realities at atlas scale**: never upload, always path; always
backed; downsample to render.

---

## 1. Why path, not upload, at this scale

CellScope has two load routes (CONTRACT §4.1, §4.2):

- `POST /api/datasets/upload` — multipart upload, capped at
  `CELLSCOPE_MAX_UPLOAD_MB` (default **5120 MB** = 5 GB) and bounded by your
  browser/proxy.
- `POST /api/datasets/load` — load a file **already on the server's disk** by
  path. No size cap, no upload round-trip.

At 1M+ cells your `.h5ad` is routinely **5–50+ GB**. **Always** stage it into
the server's `CELLSCOPE_DATA_DIR` and **Load by path**. The drag-and-drop /
upload route is for small files.

```bash
# The data dir CellScope scans (CONTRACT §3). With docker compose, mount a host
# folder to this path so big files never go through the browser.
mkdir -p data
```

---

## 2. Export a large `.h5ad` from CELLxGENE Census

CZ CELLxGENE **Census** is the most convenient source of atlas-scale,
consistently-annotated data, and it gives you exactly the columns CellScope
wants (`cell_type`, `tissue`, `assay`, plus embeddings for some collections).

### Install the Census client (prep env only)

```bash
pip install "cellxgene-census" "scanpy>=1.10" "anndata>=0.10"
```

### Export a slice to `.h5ad`

Save as `export_census.py`. The snippet below pulls **all T cells across blood
and lung** from the human Census — typically **1–3M cells** — and writes an
`.h5ad`. **Choose your filter to control the size**; the `value_filter` is the
knob.

```python
# SPDX-License-Identifier: GPL-3.0-or-later
"""Export an atlas-scale slice from CZ CELLxGENE Census to a CellScope .h5ad."""

import logging
import cellxgene_census
import scanpy as sc

logging.basicConfig(level=logging.INFO)

# Pin a Census release for reproducibility (omit for "latest").
CENSUS_VERSION = "2024-07-01"

with cellxgene_census.open_soma(census_version=CENSUS_VERSION) as census:
    # value_filter is SQL-like. Widen/narrow it to control cell count.
    # This pulls T cells from blood + lung; expect ~1-3M cells.
    adata = cellxgene_census.get_anndata(
        census,
        organism="Homo sapiens",
        obs_value_filter=(
            "cell_type in ['T cell', 'CD4-positive, alpha-beta T cell', "
            "'CD8-positive, alpha-beta T cell'] "
            "and tissue_general in ['blood', 'lung'] "
            "and is_primary_data == True"
        ),
        # Trim columns you don't need to keep the file lean.
        obs_column_names=[
            "cell_type", "tissue", "tissue_general", "assay",
            "disease", "sex", "dataset_id",
        ],
        # X is normalized log expression in Census; good for coloring.
        X_name="normalized",
    )

logging.info("exported: %d cells x %d genes", adata.n_obs, adata.n_vars)

# Census var is indexed by Ensembl ID; add symbols so gene search is friendly.
# feature_name holds the symbol.
if "feature_name" in adata.var:
    adata.var["ensembl_id"] = adata.var_names
    adata.var_names = adata.var["feature_name"].astype(str).values
    adata.var_names_make_unique()                # CRITICAL: symbols repeat

# CellScope needs an X_* embedding. Census slices usually DON'T ship one for an
# arbitrary cross-dataset query, so compute a quick UMAP from scratch.
sc.pp.highly_variable_genes(adata, n_top_genes=2000, flavor="seurat_v3"
                            if adata.X.min() >= 0 else "seurat")
adata_hvg = adata[:, adata.var.highly_variable].copy()
sc.pp.scale(adata_hvg, max_value=10)
sc.tl.pca(adata_hvg, n_comps=50)
sc.pp.neighbors(adata_hvg, n_neighbors=15, n_pcs=50)
sc.tl.umap(adata_hvg)
adata.obsm["X_pca"] = adata_hvg.obsm["X_pca"]
adata.obsm["X_umap"] = adata_hvg.obsm["X_umap"]   # -> default_embedding

# Make discrete columns categorical so they color as legends, not gradients.
for col in ["cell_type", "tissue", "tissue_general", "assay", "disease", "sex"]:
    if col in adata.obs:
        adata.obs[col] = adata.obs[col].astype("category")

adata.write_h5ad("data/census_tcells.h5ad")
logging.info("wrote data/census_tcells.h5ad")
```

```bash
python export_census.py
```

**Expected output (illustrative):**

```text
INFO:root:exported: 1842000 cells x 36601 genes
... computing UMAP (this is the slow part: minutes on millions of cells) ...
INFO:root:wrote data/census_tcells.h5ad
```

**Expected file size:** roughly **8–25 GB** for ~1.8M cells × ~36k genes,
depending on sparsity and compression. The UMAP step is the slow part of prep —
on millions of cells it is **minutes**, and benefits from many cores.

> [!NOTE]
> **Embedding shortcut.** Some Census *collections* (and most single-study
> CELLxGENE downloads) already ship a curated `X_umap`/`X_scvi` in `obsm`. If
> you download a single dataset's `.h5ad` directly from the CELLxGENE portal,
> you can often skip the UMAP computation entirely — just confirm an `X_*` obsm
> key exists (CONTRACT §2) and rename if needed:
> `adata.obsm["X_umap"] = adata.obsm.pop("X_scVI_umap")`.

### Alternatives (same shape, different source)

- **Tabula Sapiens** — multi-organ human atlas (~500k–1.1M cells depending on
  release). Download the combined `.h5ad` from the Tabula Sapiens portal /
  figshare; it ships `X_umap` and a `cell_ontology_class` annotation. Rename to
  a `cell_type` column if you like, ensure it's categorical, and load by path.
- **HLCA (Human Lung Cell Atlas)** — ~2.4M cells, distributed as `.h5ad` via
  CELLxGENE. Ships `X_umap`/`X_scanvi_emb` and rich `ann_level_*` /
  `cell_type` columns. Same recipe: confirm an `X_*` embedding, make label
  columns categorical, load by path.

For all of these the CellScope-facing rules are identical: **one `X_*`
embedding; categorical label columns** (CONTRACT §2).

---

## 3. Load by path — backed mode is automatic

Stage the file into the data dir (it's already in `data/`). With docker compose,
mount your big-disk folder to the container's data dir so the multi-GB file is
visible without copying:

```yaml
# docker-compose.yml (excerpt) — mount a host folder of large .h5ad files
services:
  cellscope:
    volumes:
      - /mnt/bigdisk/atlases:/data        # host:container
    environment:
      - CELLSCOPE_DATA_DIR=/data
```

Then **Load by path** in the File Loader:

```text
census_tcells.h5ad
```

Or from the CLI:

```bash
curl -s -X POST http://localhost:8000/api/datasets/load \
  -H 'Content-Type: application/json' \
  -d '{"path": "census_tcells.h5ad"}' | python -m json.tool
```

**Expected `DatasetInfo` (illustrative):**

```json
{
    "dataset_id": "b2c3d4e5f60718293a4b5c6d7e8f9a0b",
    "path": "/data/census_tcells.h5ad",
    "n_obs": 1842000,
    "n_vars": 36601,
    "backed": true,
    "embeddings": ["X_pca", "X_umap"],
    "default_embedding": "X_umap",
    "obs_columns": [
        {"name": "cell_type", "kind": "categorical", "n_categories": 3,
         "categories": ["CD4-positive, alpha-beta T cell",
                        "CD8-positive, alpha-beta T cell", "T cell"]},
        {"name": "tissue", "kind": "categorical", "n_categories": 18, "categories": ["..."]},
        {"name": "tissue_general", "kind": "categorical", "n_categories": 2,
         "categories": ["blood", "lung"]},
        {"name": "assay", "kind": "categorical", "n_categories": 6, "categories": ["..."]},
        {"name": "disease", "kind": "categorical", "n_categories": 9, "categories": ["..."]},
        {"name": "sex", "kind": "categorical", "n_categories": 2,
         "categories": ["female", "male"]}
    ],
    "var_index_name": null,
    "n_genes": 36601
}
```

`"backed": true` is mandatory at this scale — the file is far over the 500 MB
threshold (CONTRACT §3). Loading stays cheap because backed mode keeps only
`obs` / `var` / `obsm` in RAM and leaves `X` on disk (CONTRACT §2). The 1.8M-row
`obs` and the `(1.8M, 2)` `X_umap` together are a few hundred MB of RAM — fine;
the 1.8M × 36k `X` is **never** loaded wholesale.

**Expected RAM at load (qualitative):** hundreds of MB (obs + obsm + var), not
the tens of GB the full `X` would cost. That is the entire point of backed mode
for atlas data.

---

## 4. The embedding transfer at scale

The viewport requests `GET /api/datasets/{id}/embedding?key=X_umap` — a Float32
buffer, **interleaved xy**, length `2 * n_obs` (CONTRACT §4.5).

**Expected transfer size:** `2 * 1,842,000 * 4 bytes ≈ 14.7 MB` over the wire
for the full embedding. The response echoes:

```text
x-cellscope-dtype: float32
x-cellscope-layout: interleaved-xy
x-cellscope-n-obs: 1842000
x-cellscope-key: X_umap
x-cellscope-bounds: -14.20,-13.88,15.07,14.63
```

The client allocates exactly `byteLength / 4` floats and asserts the count
matches `X-Cellscope-N-Obs` (CONTRACT §9.2), then feeds it to deck.gl as a
binary `getPosition` attribute, `size:2` (CONTRACT §9.4–9.5) — **no per-cell JS
objects**, so the ~15 MB lands directly on the GPU.

---

## 5. The downsampling toggle — the thing that keeps it at 60fps

Drawing **all** ~1.8M points every frame is feasible on a good GPU but expensive
on integrated graphics and during heavy interaction. CellScope's **downsampling
toggle** (in the toolbar; `downsample` / `downsampleN` in the store, CONTRACT
§7) renders a deterministic stride-sampled subset (every k-th point,
k = ceil(n_obs / downsampleN)) — a target count `downsampleN` — while
keeping the **full** dataset server-side for selection and stats.

| Toggle | What renders | When to use |
|--------|--------------|-------------|
| **Downsample ON** (default at atlas scale) | a deterministic stride-sampled subset of `downsampleN` cells (e.g. 200k) | smooth pan/zoom, weak GPUs, overview |
| **Downsample OFF** | every cell | final inspection, screenshots, strong GPU |

Behavior to expect:

- The **shape** of the embedding is preserved — a deterministic stride-sampled
  subset (every k-th point, k = ceil(n_obs / downsampleN)) of a 1.8M UMAP looks
  the same, just less dense. You lose only the densest cores' last bit of
  saturation.
- **Selection still operates on the full dataset.** When you box/lasso while
  downsampled, the client registers indices and the server resolves them against
  all `n_obs` cells (CONTRACT §4.7) — markers and recompute see everything, not
  just the rendered subset. The `notes`/counts in the stats response reflect the
  true selection size.
- Toggling is instant: it only changes which indices are uploaded to the GPU; no
  server round-trip.

**Expected frame budget (qualitative):**

| Rendered cells | Typical experience |
|----------------|--------------------|
| ~200k (downsampled) | a comfortable **60fps** pan/zoom on most hardware |
| ~1.8M (full) | smooth on a discrete GPU; choppy on integrated graphics during drag |

Set a higher `downsampleN` (more fidelity) on a strong GPU, lower it on a
laptop. See [`../performance.md`](../performance.md) for the rendering budget and
why the binary-attribute path (CONTRACT §9) is what makes even the full draw
viable.

---

## 6. Pan, zoom, and color by `cell_type`

Pan/zoom is pure client-side camera math over the already-uploaded binary
positions — no server calls, so it stays smooth regardless of `n_obs` (subject
to the GPU budget above). Zoom into a dense region; the downsample sample is
stable, so points don't shimmer as you move.

Open **Color By** → the `cell_type` categorical column. The client fetches
`GET /api/datasets/{id}/obs?column=cell_type`:

```bash
curl -s -D - -o /dev/null \
  'http://localhost:8000/api/datasets/<DATASET_ID>/obs?column=cell_type'
```

**Expected response headers** (categorical shape, CONTRACT §4.6):

```text
content-type: application/octet-stream
x-cellscope-kind: categorical
x-cellscope-n-categories: 3
x-cellscope-dtype: int32
x-cellscope-n-obs: 1842000
```

Body is `1,842,000 * 4 = 7,368,000` bytes of **Int32** codes (code `-1` =
missing). The labels for those codes come from `obs_columns[*].categories` that
arrived in the `DatasetInfo` at load (CONTRACT §4.6) — the body is just integer
codes indexing that list, so the legend is built without re-sending strings per
cell. CellScope maps codes → distinct colors (`lib/colormap.ts`) into a
`Uint8Array(n*3)` and uploads it as the deck.gl `getFillColor` attribute,
`size:3` (CONTRACT §9.5).

**Expected:** CD4 and CD8 T-cell territories paint as distinct colors;
`tissue_general` (blood vs lung) reveals which regions are tissue-specific vs
shared. Color by a single gene (`CD8A`, `GZMK`, `CCR7`) and — even downsampled —
the gradient tracks the categorical structure.

> [!NOTE]
> A continuous obs column (e.g. `total_counts`) returns the **other** shape:
> Float32 length `n_obs` with `X-Cellscope-Min`/`X-Cellscope-Max` headers
> (CONTRACT §4.6). The legend auto-scales to that range.

---

## 7. Selection, markers, and recompute at scale

The loop from recipe 02 still works, but with atlas-scale honesty:

- **Selection** sends raw **Int32** indices as `application/octet-stream`
  (CONTRACT §4.7) — selecting half a million cells is a ~2 MB binary body, not a
  giant JSON array. (This is precisely why the binary selection form exists.)
- **Markers** compare your selection against a random sample of the rest, capped
  at `CELLSCOPE_MARKER_REST_CAP` (default 50,000). On a 1.8M-cell dataset the
  "rest" is *always* capped; the response says so:

  ```json
  "notes": ["rest subsampled to 50000 of 1801000 cells (cap 50000)"]
  ```

  and `rest_cells_used` reports the cap actually applied (CONTRACT §4.7, §6.5).
  This bounds marker compute time to roughly the same as a mid-size dataset
  regardless of how many millions of cells you loaded.
- **Recompute** (Leiden / UMAP) subsets the selection `.to_memory()` (CONTRACT
  §8). Keep selections to **hundreds of thousands** of cells, not millions — the
  kNN graph and UMAP are superlinear, so a 2M-cell recompute is a server-side
  marathon. Select a region of interest, recompute *that*.

**Expected timing (qualitative / projected):**

| Operation | ~1.8M-cell atlas (backed) |
|-----------|---------------------------|
| Load (metadata + obsm) | seconds (skips `X`) |
| Embedding fetch (~15 MB) | seconds over localhost |
| One gene color (1 disk column) | tens of ms |
| `cell_type` obs color (in-RAM codes) | instant |
| Markers (selection vs 50k capped rest) | tens of seconds |
| Recompute Leiden on a ~200k selection | a few minutes |

---

## 8. Performance characteristics — the summary

What makes atlas scale work in CellScope, end to end:

1. **Backed mode** (CONTRACT §2): only `X` stays on disk; `obs`/`var`/`obsm` are
   cheap. Load and metadata are O(metadata), not O(cells × genes).
2. **Binary protocol** (CONTRACT §9): positions and per-cell scalars travel as
   raw little-endian typed arrays straight to the GPU — no per-row JS objects,
   which is the only way a browser holds millions of cells.
3. **Downsampling** (CONTRACT §7): decouples *render count* from *dataset size*.
   The full dataset stays authoritative server-side for selection/markers.
4. **One-column reads**: coloring by a gene reads a single column from disk in
   backed mode (CONTRACT §4.6) — the only routinely disk-bound interaction.
5. **Capped marker compares** (`MARKER_REST_CAP`): differential expression cost
   is bounded by the cap, not by `n_obs`.

For the full treatment — RAM math, the rendering budget, the backed-mode
marker/recompute strategy, and where the limits actually bite — see
[`../performance.md`](../performance.md).

---

## Troubleshooting

- **Upload failed / 413.** You tried to drag-drop a multi-GB file.
  `POST /api/datasets/upload` is capped at `CELLSCOPE_MAX_UPLOAD_MB` (5 GB
  default). Stage the file on disk and **Load by path** instead (step 3).
- **"no embedding" / empty viewport.** The exported `.h5ad` has no `X_*` obsm
  key. Census cross-dataset queries don't ship one — compute a UMAP in prep
  (step 2), or download a single CELLxGENE dataset that includes one.
- **`cell_type` colors as a gradient.** It's object/string but not categorical
  after some transforms. `adata.obs["cell_type"] = adata.obs["cell_type"].astype("category")`
  before `write_h5ad`.
- **Gene search returns Ensembl IDs, not symbols.** Census `var` is indexed by
  Ensembl ID. Set `var_names` from `feature_name` and run
  `var_names_make_unique()` (step 2), or search the Ensembl ID directly.
- **RAM blows up at load.** You're not in backed mode (file under threshold, or
  threshold raised above file size), or `obs` has huge object columns. Trim
  `obs_column_names` in the export (step 2) and confirm `"backed": true`.
- **Pan/zoom is choppy.** Turn the **downsampling toggle on** and/or lower
  `downsampleN` (step 5). Rendering all millions of points strains integrated
  GPUs.

Next: [recipe 04](./04-interop-scanpy-seurat-cellranger.md) — point CellScope at
files produced by Scanpy, Seurat, and Cell Ranger, with the gotchas for each.
