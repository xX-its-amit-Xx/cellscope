<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# CellScope Examples — sample & real data

This directory holds two small, self-documenting scripts that produce
browsable `.h5ad` files for CellScope:

| Script | Network? | Deps | Output (default) | Use it for |
|--------|----------|------|------------------|------------|
| [`generate_sample.py`](./generate_sample.py) | **No** | `numpy`, `scipy`, `anndata`, `pandas` | `$CELLSCOPE_DATA_DIR/sample.h5ad` (else `./data/sample.h5ad`) | An instant, offline, reproducible synthetic dataset — what the Docker image auto-generates on first run. |
| [`download_pbmc3k.py`](./download_pbmc3k.py) | **Yes** | `scanpy` (+ `leidenalg`, `igraph`) | `$CELLSCOPE_DATA_DIR/pbmc3k.h5ad` (else `./data/pbmc3k.h5ad`) | A *real* 10x PBMC 3k dataset, prepared with a standard scanpy pipeline. |

Both write a file that satisfies the CellScope loader contract: at least one
`X_*` embedding in `obsm` and properly typed `obs` columns (categorical →
discrete legend, numeric → continuous colormap). See
[`../docs/CONTRACT.md`](../docs/CONTRACT.md) §2 for the loader rules.

---

## `generate_sample.py` — synthetic, offline, fast

A self-contained generator that fabricates a realistic small dataset using only
`numpy` + `scipy` + `anndata` (no `scanpy`, **no network**). It runs in a few
seconds with modest RAM, so it is safe to run anywhere — including inside the
container at startup.

### Run it

```bash
# Defaults: 20,000 cells x 2,000 genes -> ./data/sample.h5ad
python examples/generate_sample.py

# Custom size and explicit output path
python examples/generate_sample.py --n-cells 50000 --n-genes 3000 --out ./data/big_sample.h5ad

# A tiny file for smoke tests
python examples/generate_sample.py --n-cells 500 --n-genes 200 --out /tmp/tiny.h5ad
```

### CLI options

| Flag | Default | Meaning |
|------|---------|---------|
| `--n-cells` | `20000` | number of cells (rows of `X`) |
| `--n-genes` | `2000` | number of genes (columns of `X`) |
| `--n-pca` | `30` | components stored in `obsm['X_pca']` |
| `--seed` | `0` | random seed (full reproducibility) |
| `--out` | *(see below)* | output `.h5ad` path |

### Output path precedence (matches the entrypoint)

The `--out` value wins. When omitted, the script falls back exactly the way
`docker/entrypoint.sh` expects:

1. `--out <path>` if given;
2. `$CELLSCOPE_DATA_DIR/sample.h5ad` if `CELLSCOPE_DATA_DIR` is set;
3. `./data/sample.h5ad`.

`CELLSCOPE_DATA_DIR` defaults to `./data` (CONTRACT §3), so a bare
`python examples/generate_sample.py` lands the file where the server scans for
datasets.

### What the synthetic dataset looks like

For the default `20000 x 2000` run the generator produces:

- **`X`** — a `scipy.csr_matrix` of `float32`, integer-valued (Poisson) counts.
  It is sparse-ish: a low-rate per-gene baseline plus a strong per-cluster boost
  on that cluster's marker genes, so differential expression is recoverable by
  `rank_genes_groups` and visibly tracks the UMAP blobs.
- **`obs` columns**
  - `cell_type` — **categorical** (human-readable names: `T cell`, `B cell`,
    `NK cell`, `Monocyte`, `Megakaryocyte`, ...). Drives discrete coloring.
  - `leiden` — **categorical** (stringified cluster ids `"0"`, `"1"`, ...). Drives
    discrete coloring and mirrors what the recompute feature would yield.
  - `total_counts` — **continuous** (`float32`, per-cell library size). Drives
    continuous coloring.
- **`obsm` embeddings**
  - `X_umap` — `(n, 2)` `float32`. Laid out as **one well-separated Gaussian blob
    per cluster** arranged around a ring, so the scatter looks like a real UMAP.
    This is the `default_embedding` (CONTRACT §2).
  - `X_pca` — `(n, ~30)` `float32`, with leading components that separate clusters.
- **`var_names`** — mostly `Gene_0 ... Gene_{n-1}`, with a handful of recognizable
  marker symbols (`CD3D`, `MS4A1`, `CD19`, `NKG7`, `LYZ`, `PPBP`) injected into the
  marker block of specific clusters. Coloring by, e.g., `CD3D` lights up the
  T-cell blob.
- **`uns['cellscope_sample']`** — provenance (`generator`, `n_clusters`, `seed`).

The number of clusters is chosen automatically from the requested size (2–8) so
the data stays sensible at every scale, and each cluster is guaranteed at least
one cell. On completion the script logs a summary (file size, shape, density,
obs columns, obsm keys, cell types, and which markers were embedded).

### Try it in the browser

Once a server is running on <http://localhost:8000> (see
[the cookbook prerequisites](../docs/cookbook/README.md#prerequisites)):

1. Load `sample.h5ad` by path in the **File Loader** (relative names resolve
   against `CELLSCOPE_DATA_DIR`).
2. Color by `cell_type` or `leiden` for a discrete legend, or by `total_counts`
   for a gradient.
3. Color by gene `CD3D` / `LYZ` / `MS4A1` / `NKG7` — each lights up its cluster.
4. Box- or lasso-select a blob and compute markers; the injected marker genes
   should top the list for their cluster.

---

## How the Docker entrypoint auto-generates `sample.h5ad`

You do **not** have to run anything to get started: the container does it for
you. On first start, `docker/entrypoint.sh`:

1. ensures `CELLSCOPE_DATA_DIR` (default `/data` in the image) exists;
2. scans it for any top-level `.h5ad`;
3. if none is found, runs
   `python examples/generate_sample.py --out "$CELLSCOPE_DATA_DIR/sample.h5ad"`;
4. sets `CELLSCOPE_AUTOLOAD` to that file so the API loads it at startup.

So `docker compose up` boots straight into a browsable synthetic dataset. Drop
your own `.h5ad` into the mounted data dir (or set `CELLSCOPE_AUTOLOAD`) and the
generator is skipped — your file is used instead. This is why the `--out` flag
and its `$CELLSCOPE_DATA_DIR/sample.h5ad` default exist and must stay in sync
with the entrypoint.

---

## `download_pbmc3k.py` — the real 10x PBMC 3k dataset

> **Requires a network connection and `scanpy`.** This script downloads data
> from the scanpy dataset cache / 10x servers and uses scanpy's preprocessing.
> If you only need offline, fast data, use `generate_sample.py` instead.

It fetches the canonical 10x PBMC 3k peripheral-blood dataset and produces a
browsable `.h5ad` with an `X_umap` embedding and a `leiden` clustering.

### Install the prep dependencies

```bash
python -m venv .venv-prep
source .venv-prep/bin/activate        # Windows: .venv-prep\Scripts\activate
pip install "scanpy>=1.10" "anndata>=0.10" leidenalg igraph
```

### Run it

```bash
# Fast path: pre-processed object (default) -> ./data/pbmc3k.h5ad
python examples/download_pbmc3k.py

# Run the full pipeline on the raw counts instead
python examples/download_pbmc3k.py --source raw --out ./data/pbmc3k.h5ad
```

### CLI options

| Flag | Default | Meaning |
|------|---------|---------|
| `--source` | `processed` | `processed` = `sc.datasets.pbmc3k_processed()` (fast, pre-annotated); `raw` = `sc.datasets.pbmc3k()` then the full standard pipeline. |
| `--out` | `$CELLSCOPE_DATA_DIR/pbmc3k.h5ad` else `./data/pbmc3k.h5ad` | output `.h5ad` path |

### What each source does

- **`processed`** — loads the pre-filtered, normalized, log-transformed object.
  It already carries an annotation (`louvain`) and usually an embedding; the
  script guarantees both `X_umap` and `leiden` exist, computing whatever is
  missing (reusing `louvain` as `leiden` when present).
- **`raw`** — runs the canonical scanpy PBMC 3k pipeline: QC filtering →
  `normalize_total` → `log1p` → highly-variable genes → scale → PCA → neighbors →
  UMAP → Leiden. The full log-normalized matrix is preserved in `adata.raw` so
  gene-expression coloring stays interpretable. (Note: CellScope colors from
  `adata.X`; this script keeps `X` log-normalized for exactly that reason — see
  the "raw vs normalized X" gotcha in
  [recipe 04](../docs/cookbook/04-interop-scanpy-seurat-cellranger.md).)

The result loads in CellScope exactly like the synthetic file. The full
walkthrough — load, color by `LYZ`/`CD3D`/`MS4A1`/`NKG7`, box-select a cluster,
and read its top markers — is **recipe 01** in the cookbook:
[`../docs/cookbook/01-pbmc3k-quickstart.md`](../docs/cookbook/01-pbmc3k-quickstart.md).

---

## Getting more / larger real data

The [CellScope cookbook](../docs/cookbook/README.md) has end-to-end recipes for
real public datasets, including larger ones that exercise backed mode,
downsampling, and on-the-fly recompute:

- [01 — PBMC 3k quickstart](../docs/cookbook/01-pbmc3k-quickstart.md) (this script)
- [02 — PBMC 68k markers](../docs/cookbook/02-pbmc68k-markers.md) (backed mode, sub-clustering)
- [03 — Atlas scale](../docs/cookbook/03-atlas-scale.md) (1M+ cells, performance)
- [04 — Interop: Scanpy / Seurat / Cell Ranger](../docs/cookbook/04-interop-scanpy-seurat-cellranger.md) (bring your own data)

> [!NOTE]
> Per the project's hard constraints, these scripts are **not** run during the
> docs/CI build — only `generate_sample.py` is executed at container startup.
> Numbers printed in the cookbook are illustrative; real values depend on the
> dataset snapshot, your scanpy version, and hardware.
