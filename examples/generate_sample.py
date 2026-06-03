# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthesize a small, realistic single-cell dataset for CellScope.

This script is **self-contained, fast, and network-free**. It uses only ``numpy``
and ``anndata`` (``scanpy`` is *not* required) to fabricate a browsable ``.h5ad``
file that exercises every feature of the CellScope browser:

* a sparse-ish, integer-valued count matrix ``X`` with several simulated
  cell-type clusters (each cluster over-expresses a distinct set of marker genes);
* an ``obs`` categorical column ``cell_type`` and a ``leiden`` clustering column;
* an ``obs`` continuous column ``total_counts``;
* an ``obsm['X_umap']`` embedding (``n, 2``) laid out as well-separated Gaussian
  blobs — one per cluster — so the UMAP visually resembles real clusters;
* an ``obsm['X_pca']`` embedding (``n, ~30``);
* ``var_names`` of the form ``Gene_0 ... Gene_{k}`` plus a handful of recognizable
  marker symbols (``CD3D``, ``CD19``, ``NKG7``, ``LYZ``, ``PPBP``, ``MS4A1``)
  that are deliberately assigned to particular clusters.

The defaults (20000 cells x 2000 genes) produce a file that loads in CellScope's
in-memory (non-backed) mode and is generated in a few seconds with modest RAM.

Output path resolution (highest priority first):

1. the ``--out`` CLI argument, if given;
2. ``$CELLSCOPE_DATA_DIR/sample.h5ad`` if ``CELLSCOPE_DATA_DIR`` is set;
3. ``./data/sample.h5ad``.

This mirrors ``docker/entrypoint.sh``, which auto-generates the sample dataset
into ``$CELLSCOPE_DATA_DIR`` on first start when no data is present.

Example:
    python examples/generate_sample.py --n-cells 20000 --n-genes 2000
    python examples/generate_sample.py --out /tmp/tiny.h5ad --n-cells 500
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import anndata as ad
import numpy as np
from scipy import sparse

logger = logging.getLogger("cellscope.generate_sample")

# Recognizable marker symbols assigned (in order) to the first clusters. Each
# tuple is (symbol, cluster_index) so the cluster that "should" express the
# marker does so strongly, making colour-by-gene visibly track the UMAP blobs.
MARKER_SYMBOLS: tuple[str, ...] = (
    "CD3D",   # T cells
    "MS4A1",  # B cells
    "CD19",   # B cells
    "NKG7",   # NK cells
    "LYZ",    # Monocytes
    "PPBP",   # Megakaryocytes / platelets
)

# Human-friendly cell-type names cycled across the simulated clusters. The first
# entries line up with the marker symbols above for biological plausibility.
CELL_TYPE_NAMES: tuple[str, ...] = (
    "T cell",
    "B cell",
    "NK cell",
    "Monocyte",
    "Megakaryocyte",
    "Dendritic cell",
    "Plasma cell",
    "Progenitor",
)


@dataclass(frozen=True)
class SampleSpec:
    """Parameters describing the synthetic dataset to generate.

    Attributes:
        n_cells: Total number of cells (rows of ``X``).
        n_genes: Total number of genes (columns of ``X``).
        n_clusters: Number of simulated cell-type clusters.
        n_pca: Number of PCA components stored in ``obsm['X_pca']``.
        seed: Random seed for full reproducibility.
    """

    n_cells: int
    n_genes: int
    n_clusters: int
    n_pca: int
    seed: int


def _resolve_out_path(out: str | None) -> Path:
    """Resolve the output ``.h5ad`` path following the documented precedence.

    Args:
        out: The explicit ``--out`` value, or ``None`` to fall back to the
            ``CELLSCOPE_DATA_DIR`` environment variable / ``./data``.

    Returns:
        The absolute output path for the generated ``sample.h5ad`` file.
    """
    if out:
        return Path(out).expanduser().resolve()
    data_dir = os.environ.get("CELLSCOPE_DATA_DIR", "").strip() or "./data"
    return (Path(data_dir).expanduser() / "sample.h5ad").resolve()


def _choose_n_clusters(n_cells: int, n_genes: int) -> int:
    """Pick a sensible cluster count for the requested dataset size.

    Keeps clusters meaningfully populated for tiny datasets while capping the
    count so each cluster still owns a distinct block of marker genes.

    Args:
        n_cells: Requested number of cells.
        n_genes: Requested number of genes.

    Returns:
        A cluster count in ``[2, 8]`` that fits the data dimensions.
    """
    upper = min(8, max(2, n_cells // 200), max(2, n_genes // 100))
    return int(max(2, upper))


def _assign_clusters(rng: np.random.Generator, n_cells: int, n_clusters: int) -> np.ndarray:
    """Assign each cell to a cluster with mildly uneven cluster sizes.

    Args:
        rng: Seeded NumPy random generator.
        n_cells: Number of cells to assign.
        n_clusters: Number of clusters.

    Returns:
        An ``int`` array of shape ``(n_cells,)`` with values in
        ``[0, n_clusters)``; every cluster is guaranteed at least one cell.
    """
    weights = rng.uniform(0.6, 1.4, size=n_clusters)
    probs = weights / weights.sum()
    labels = rng.choice(n_clusters, size=n_cells, p=probs)
    # Guarantee non-empty clusters by seeding one cell of each.
    seed_cells = rng.choice(n_cells, size=n_clusters, replace=False)
    labels[seed_cells] = np.arange(n_clusters)
    return labels.astype(np.int64)


def _build_marker_layout(spec: SampleSpec) -> tuple[list[str], dict[int, list[int]]]:
    """Construct ``var_names`` and the per-cluster marker-gene index map.

    Most genes are named ``Gene_<i>``. A contiguous block of genes per cluster is
    designated as that cluster's markers; the recognizable symbols in
    :data:`MARKER_SYMBOLS` replace the first marker name of their assigned
    cluster so colour-by-gene on, e.g., ``CD3D`` lights up the T-cell blob.

    Args:
        spec: The dataset specification.

    Returns:
        A tuple ``(var_names, cluster_markers)`` where ``var_names`` is the list
        of gene names of length ``n_genes`` and ``cluster_markers`` maps each
        cluster index to the list of gene indices that cluster over-expresses.
    """
    var_names = [f"Gene_{i}" for i in range(spec.n_genes)]

    # Reserve a block of marker genes per cluster from the front of the matrix.
    markers_per_cluster = max(1, min(20, spec.n_genes // (spec.n_clusters * 4) or 1))
    cluster_markers: dict[int, list[int]] = {}
    cursor = 0
    for cluster in range(spec.n_clusters):
        idxs = list(range(cursor, min(cursor + markers_per_cluster, spec.n_genes)))
        cluster_markers[cluster] = idxs
        cursor += markers_per_cluster

    # Inject recognizable symbols at the first marker slot of their cluster.
    for offset, symbol in enumerate(MARKER_SYMBOLS):
        cluster = offset % spec.n_clusters
        if cluster_markers[cluster]:
            var_names[cluster_markers[cluster][0]] = symbol

    return var_names, cluster_markers


def _simulate_counts(
    rng: np.random.Generator,
    spec: SampleSpec,
    labels: np.ndarray,
    cluster_markers: dict[int, list[int]],
) -> sparse.csr_matrix:
    """Simulate an integer, sparse-ish count matrix with cluster structure.

    Baseline expression is drawn from a low-rate Poisson for every gene. For each
    cell, the genes belonging to that cell's cluster receive a strong additive
    Poisson boost, producing clear differential expression that ``rank_genes_groups``
    (and visual colour-by-gene) can recover.

    Args:
        rng: Seeded NumPy random generator.
        spec: The dataset specification.
        labels: Per-cell cluster assignments, shape ``(n_cells,)``.
        cluster_markers: Mapping of cluster index to its marker gene indices.

    Returns:
        A CSR sparse matrix of shape ``(n_cells, n_genes)`` with ``float32``
        integer-valued counts.
    """
    n_cells, n_genes = spec.n_cells, spec.n_genes

    # Per-gene baseline mean expression rate; most genes are lowly expressed.
    base_rate = rng.gamma(shape=0.3, scale=0.7, size=n_genes).astype(np.float32)

    rows: list[np.ndarray] = []
    cols: list[np.ndarray] = []
    vals: list[np.ndarray] = []

    # A boost mean drawn once per cluster so marker strength varies by cluster.
    cluster_boost = {c: float(rng.uniform(6.0, 14.0)) for c in cluster_markers}

    # Process in row chunks to keep peak memory modest for large n_cells.
    chunk = 2048
    for start in range(0, n_cells, chunk):
        stop = min(start + chunk, n_cells)
        block = rng.poisson(base_rate[None, :], size=(stop - start, n_genes))

        # Apply per-cluster marker boosts to the cells in this chunk.
        block_labels = labels[start:stop]
        for cluster, gene_idxs in cluster_markers.items():
            if not gene_idxs:
                continue
            sel = np.flatnonzero(block_labels == cluster)
            if sel.size == 0:
                continue
            boost = rng.poisson(
                cluster_boost[cluster], size=(sel.size, len(gene_idxs))
            )
            block[np.ix_(sel, gene_idxs)] += boost

        block = block.astype(np.float32, copy=False)
        nz_r, nz_c = np.nonzero(block)
        if nz_r.size:
            rows.append(nz_r + start)
            cols.append(nz_c)
            vals.append(block[nz_r, nz_c])

    if rows:
        data = np.concatenate(vals).astype(np.float32, copy=False)
        row_idx = np.concatenate(rows)
        col_idx = np.concatenate(cols)
    else:  # pragma: no cover - only for pathological tiny inputs
        data = np.empty(0, dtype=np.float32)
        row_idx = np.empty(0, dtype=np.int64)
        col_idx = np.empty(0, dtype=np.int64)

    matrix = sparse.csr_matrix(
        (data, (row_idx, col_idx)), shape=(n_cells, n_genes), dtype=np.float32
    )
    matrix.eliminate_zeros()
    return matrix


def _make_umap(
    rng: np.random.Generator, labels: np.ndarray, n_clusters: int
) -> np.ndarray:
    """Lay out a UMAP-like embedding as one Gaussian blob per cluster.

    Cluster centres are spread around a circle so blobs are well separated and
    the resulting scatter visually resembles a real UMAP.

    Args:
        rng: Seeded NumPy random generator.
        labels: Per-cell cluster assignments.
        n_clusters: Number of clusters.

    Returns:
        A ``float32`` array of shape ``(n_cells, 2)``.
    """
    radius = 10.0 + 1.5 * n_clusters
    angles = np.linspace(0.0, 2.0 * np.pi, n_clusters, endpoint=False)
    centres = np.column_stack([radius * np.cos(angles), radius * np.sin(angles)])
    # Slight per-cluster jitter so the ring is not perfectly regular.
    centres += rng.normal(0.0, 1.5, size=centres.shape)

    spreads = rng.uniform(1.2, 2.4, size=n_clusters)
    coords = centres[labels] + rng.normal(0.0, 1.0, size=(labels.size, 2)) * spreads[
        labels
    ][:, None]
    return coords.astype(np.float32, copy=False)


def _make_pca(
    rng: np.random.Generator, labels: np.ndarray, n_clusters: int, n_pca: int
) -> np.ndarray:
    """Synthesize a PCA embedding whose leading components separate clusters.

    Args:
        rng: Seeded NumPy random generator.
        labels: Per-cell cluster assignments.
        n_clusters: Number of clusters.
        n_pca: Number of PCA components.

    Returns:
        A ``float32`` array of shape ``(n_cells, n_pca)``.
    """
    centres = rng.normal(0.0, 6.0, size=(n_clusters, n_pca)).astype(np.float32)
    noise = rng.normal(0.0, 1.0, size=(labels.size, n_pca)).astype(np.float32)
    return (centres[labels] + noise).astype(np.float32, copy=False)


def build_anndata(spec: SampleSpec) -> ad.AnnData:
    """Build a complete, browsable :class:`anndata.AnnData` from a spec.

    Args:
        spec: The dataset specification.

    Returns:
        An in-memory ``AnnData`` with a sparse ``X``, the ``obs`` columns
        ``cell_type``/``leiden``/``total_counts``, and the ``obsm`` embeddings
        ``X_umap`` and ``X_pca``.
    """
    import pandas as pd  # local import keeps module import light

    rng = np.random.default_rng(spec.seed)

    labels = _assign_clusters(rng, spec.n_cells, spec.n_clusters)
    var_names, cluster_markers = _build_marker_layout(spec)
    matrix = _simulate_counts(rng, spec, labels, cluster_markers)
    umap = _make_umap(rng, labels, spec.n_clusters)
    pca = _make_pca(rng, labels, spec.n_clusters, spec.n_pca)

    total_counts = np.asarray(matrix.sum(axis=1)).ravel().astype(np.float32)

    cell_type_labels = [
        CELL_TYPE_NAMES[c % len(CELL_TYPE_NAMES)] for c in labels.tolist()
    ]
    obs = pd.DataFrame(
        {
            "cell_type": pd.Categorical(cell_type_labels),
            "leiden": pd.Categorical([str(int(c)) for c in labels.tolist()]),
            "total_counts": total_counts,
        },
        index=[f"Cell_{i}" for i in range(spec.n_cells)],
    )
    var = pd.DataFrame(index=pd.Index(var_names, name="gene_symbol"))

    adata = ad.AnnData(X=matrix, obs=obs, var=var)
    adata.obsm["X_umap"] = umap
    adata.obsm["X_pca"] = pca
    adata.uns["cellscope_sample"] = {
        "generator": "examples/generate_sample.py",
        "n_clusters": spec.n_clusters,
        "seed": spec.seed,
    }
    return adata


def generate(spec: SampleSpec, out_path: Path) -> ad.AnnData:
    """Generate the dataset and write it to ``out_path``.

    Args:
        spec: The dataset specification.
        out_path: Destination ``.h5ad`` path; parent dirs are created.

    Returns:
        The generated ``AnnData`` (also written to disk).
    """
    logger.info(
        "Generating sample: %d cells x %d genes, %d clusters (seed=%d)",
        spec.n_cells,
        spec.n_genes,
        spec.n_clusters,
        spec.seed,
    )
    adata = build_anndata(spec)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(out_path, compression="gzip")
    return adata


def _summarize(adata: ad.AnnData, out_path: Path) -> None:
    """Log a human-readable summary of the generated dataset.

    Args:
        adata: The generated AnnData.
        out_path: Where it was written.
    """
    density = adata.X.nnz / float(adata.n_obs * adata.n_vars) if adata.n_vars else 0.0
    size_mb = out_path.stat().st_size / (1024 * 1024) if out_path.exists() else 0.0
    cell_types = list(adata.obs["cell_type"].cat.categories)

    logger.info("Wrote %s (%.2f MB)", out_path, size_mb)
    logger.info("  shape:      %d cells x %d genes", adata.n_obs, adata.n_vars)
    logger.info("  X:          %s, nnz=%d, density=%.4f", type(adata.X).__name__, adata.X.nnz, density)
    logger.info("  obs cols:   %s", ", ".join(adata.obs.columns))
    logger.info("  obsm keys:  %s", ", ".join(adata.obsm.keys()))
    logger.info("  X_umap:     %s", adata.obsm["X_umap"].shape)
    logger.info("  X_pca:      %s", adata.obsm["X_pca"].shape)
    logger.info("  cell_type:  %s", ", ".join(cell_types))
    present = [m for m in MARKER_SYMBOLS if m in set(adata.var_names)]
    logger.info("  markers:    %s", ", ".join(present) if present else "(none — too few genes)")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Optional explicit argument vector (defaults to ``sys.argv``).

    Returns:
        The parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description="Generate a synthetic, network-free CellScope sample dataset (.h5ad).",
    )
    parser.add_argument(
        "--n-cells", type=int, default=20000, help="number of cells (default: 20000)"
    )
    parser.add_argument(
        "--n-genes", type=int, default=2000, help="number of genes (default: 2000)"
    )
    parser.add_argument(
        "--n-pca", type=int, default=30, help="PCA components in X_pca (default: 30)"
    )
    parser.add_argument(
        "--seed", type=int, default=0, help="random seed (default: 0)"
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="output .h5ad path (default: $CELLSCOPE_DATA_DIR/sample.h5ad or ./data/sample.h5ad)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse args, generate the dataset, print a summary.

    Args:
        argv: Optional explicit argument vector.

    Returns:
        Process exit code (``0`` on success, ``2`` on invalid arguments).
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = parse_args(argv)

    if args.n_cells < 2 or args.n_genes < 2:
        logger.error("--n-cells and --n-genes must each be >= 2")
        return 2
    if args.n_pca < 2 or args.n_pca >= args.n_genes:
        logger.error("--n-pca must be in [2, n_genes)")
        return 2

    spec = SampleSpec(
        n_cells=int(args.n_cells),
        n_genes=int(args.n_genes),
        n_clusters=_choose_n_clusters(int(args.n_cells), int(args.n_genes)),
        n_pca=int(args.n_pca),
        seed=int(args.seed),
    )
    out_path = _resolve_out_path(args.out)
    adata = generate(spec, out_path)
    _summarize(adata, out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
