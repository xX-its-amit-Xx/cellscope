# SPDX-License-Identifier: GPL-3.0-or-later
"""Fetch and prepare the real 10x Genomics PBMC 3k dataset for CellScope.

Unlike :mod:`examples.generate_sample`, this script **requires a network
connection and scanpy**. It downloads the well-known PBMC 3k peripheral blood
mononuclear cell dataset published by 10x Genomics and produces a browsable
``.h5ad`` carrying an ``X_umap`` embedding and a ``leiden`` clustering — exactly
what the CellScope browser needs to colour-by-cluster and explore.

Two sources are supported:

* ``--source processed`` (default): ``scanpy.datasets.pbmc3k_processed()`` — a
  pre-filtered, normalized, log-transformed object that already contains a
  ``louvain`` annotation and (depending on the scanpy version) a UMAP. We ensure
  both an ``X_umap`` embedding and a ``leiden`` column exist, computing whatever
  is missing with a minimal standard pipeline. This is the fastest, most
  reproducible option.
* ``--source raw``: ``scanpy.datasets.pbmc3k()`` — the raw count matrix. We run
  a minimal but standard preprocessing pipeline
  (filter -> normalize -> log1p -> HVG -> scale -> PCA -> neighbors -> UMAP ->
  leiden) to produce a browsable result.

Network and dependency note:
    This script will reach out to the scanpy dataset cache / 10x servers on first
    run and writes a cache under scanpy's data directory. It needs ``scanpy``
    (and its ``leidenalg``/``igraph`` extras for clustering) installed. If you
    only need offline, fast test data, use ``examples/generate_sample.py``
    instead.

Output path resolution (highest priority first):

1. the ``--out`` CLI argument, if given;
2. ``$CELLSCOPE_DATA_DIR/pbmc3k.h5ad`` if ``CELLSCOPE_DATA_DIR`` is set;
3. ``./data/pbmc3k.h5ad``.

Example:
    python examples/download_pbmc3k.py
    python examples/download_pbmc3k.py --source raw --out ./data/pbmc3k.h5ad
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import anndata as ad

logger = logging.getLogger("cellscope.download_pbmc3k")


def _resolve_out_path(out: str | None) -> Path:
    """Resolve the output ``.h5ad`` path following the documented precedence.

    Args:
        out: The explicit ``--out`` value, or ``None`` to fall back to the
            ``CELLSCOPE_DATA_DIR`` environment variable / ``./data``.

    Returns:
        The absolute output path for the generated ``pbmc3k.h5ad`` file.
    """
    if out:
        return Path(out).expanduser().resolve()
    data_dir = os.environ.get("CELLSCOPE_DATA_DIR", "").strip() or "./data"
    return (Path(data_dir).expanduser() / "pbmc3k.h5ad").resolve()


def _ensure_leiden(adata: "ad.AnnData") -> None:
    """Guarantee an ``obs['leiden']`` categorical column exists.

    If the object already carries ``leiden`` it is left untouched. Otherwise an
    existing ``louvain`` annotation (present in ``pbmc3k_processed``) is copied
    across; failing that, Leiden clustering is computed in place (which assumes a
    neighbours graph has already been built).

    Args:
        adata: The dataset to annotate, modified in place.
    """
    import scanpy as sc

    if "leiden" in adata.obs:
        return
    if "louvain" in adata.obs:
        logger.info("Reusing existing 'louvain' annotation as 'leiden'")
        adata.obs["leiden"] = adata.obs["louvain"].astype("category")
        return
    logger.info("Computing Leiden clustering")
    try:
        sc.tl.leiden(adata, resolution=1.0, flavor="igraph", n_iterations=2, directed=False)
    except TypeError:
        # Older scanpy without the igraph flavor signature.
        sc.tl.leiden(adata, resolution=1.0)


def _ensure_umap(adata: "ad.AnnData") -> None:
    """Guarantee an ``obsm['X_umap']`` embedding exists.

    Builds a neighbours graph (computing PCA first if absent) and runs UMAP only
    when no embedding is present, so pre-embedded inputs are left untouched.

    Args:
        adata: The dataset to embed, modified in place.
    """
    import scanpy as sc

    if "X_umap" in adata.obsm:
        return
    if "X_pca" not in adata.obsm:
        logger.info("Computing PCA")
        sc.pp.pca(adata, n_comps=min(50, adata.n_vars - 1, adata.n_obs - 1))
    if "neighbors" not in adata.uns:
        logger.info("Building neighbours graph")
        sc.pp.neighbors(adata, n_neighbors=15)
    logger.info("Computing UMAP")
    sc.tl.umap(adata)


def load_processed() -> "ad.AnnData":
    """Load the pre-processed PBMC 3k object via scanpy.

    Returns:
        An ``AnnData`` with normalized, log-transformed expression and (after
        :func:`_ensure_umap` / :func:`_ensure_leiden`) ``X_umap`` + ``leiden``.
    """
    import scanpy as sc

    logger.info("Downloading scanpy.datasets.pbmc3k_processed() ...")
    adata = sc.datasets.pbmc3k_processed()
    adata.var_names_make_unique()
    _ensure_umap(adata)
    _ensure_leiden(adata)
    return adata


def load_raw() -> "ad.AnnData":
    """Load raw PBMC 3k counts and run a minimal standard scanpy pipeline.

    The pipeline mirrors the canonical scanpy PBMC 3k tutorial: basic QC
    filtering, total-count normalization, ``log1p``, highly-variable-gene
    selection, scaling, PCA, neighbours, UMAP, and Leiden clustering.

    Returns:
        A browsable ``AnnData`` with ``X_umap`` and ``leiden``. The unscaled
        log-normalized matrix is preserved in ``adata.raw`` so expression
        colouring shows interpretable values.
    """
    import scanpy as sc

    logger.info("Downloading scanpy.datasets.pbmc3k() ...")
    adata = sc.datasets.pbmc3k()
    adata.var_names_make_unique()

    logger.info("Filtering cells/genes")
    sc.pp.filter_cells(adata, min_genes=200)
    sc.pp.filter_genes(adata, min_cells=3)

    # Mitochondrial QC, then drop likely-dead cells.
    adata.var["mt"] = adata.var_names.str.startswith("MT-")
    sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], percent_top=None, log1p=False, inplace=True)
    adata = adata[adata.obs["pct_counts_mt"] < 5.0].copy()

    logger.info("Normalizing + log1p")
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)

    logger.info("Selecting highly variable genes")
    sc.pp.highly_variable_genes(adata, min_mean=0.0125, max_mean=3, min_disp=0.5)
    adata.raw = adata  # keep full log-normalized matrix for expression colouring
    adata = adata[:, adata.var["highly_variable"]].copy()

    sc.pp.scale(adata, max_value=10)
    logger.info("PCA -> neighbours -> UMAP -> Leiden")
    sc.pp.pca(adata, n_comps=50)
    sc.pp.neighbors(adata, n_neighbors=15, n_pcs=40)
    sc.tl.umap(adata)
    try:
        sc.tl.leiden(adata, resolution=1.0, flavor="igraph", n_iterations=2, directed=False)
    except TypeError:
        sc.tl.leiden(adata, resolution=1.0)

    return adata


def _summarize(adata: "ad.AnnData", out_path: Path) -> None:
    """Log a human-readable summary of the prepared dataset.

    Args:
        adata: The prepared AnnData.
        out_path: Where it was written.
    """
    size_mb = out_path.stat().st_size / (1024 * 1024) if out_path.exists() else 0.0
    n_clusters = (
        int(adata.obs["leiden"].nunique()) if "leiden" in adata.obs else 0
    )
    logger.info("Wrote %s (%.2f MB)", out_path, size_mb)
    logger.info("  shape:     %d cells x %d genes", adata.n_obs, adata.n_vars)
    logger.info("  obsm keys: %s", ", ".join(adata.obsm.keys()))
    logger.info("  leiden:    %d clusters", n_clusters)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Optional explicit argument vector (defaults to ``sys.argv``).

    Returns:
        The parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description="Download the real 10x PBMC 3k dataset and prepare a browsable .h5ad (needs network + scanpy).",
    )
    parser.add_argument(
        "--source",
        choices=("processed", "raw"),
        default="processed",
        help="'processed' (fast, pre-annotated) or 'raw' (run full pipeline). Default: processed",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="output .h5ad path (default: $CELLSCOPE_DATA_DIR/pbmc3k.h5ad or ./data/pbmc3k.h5ad)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point: download PBMC 3k, prepare it, and write the ``.h5ad``.

    Args:
        argv: Optional explicit argument vector.

    Returns:
        Process exit code (``0`` on success, ``1`` if scanpy is unavailable).
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = parse_args(argv)

    try:
        import scanpy  # noqa: F401  (import probe)
    except ImportError:
        logger.error(
            "scanpy is required for this script. Install it with "
            "'pip install scanpy leidenalg igraph', or use "
            "examples/generate_sample.py for offline data."
        )
        return 1

    out_path = _resolve_out_path(args.out)
    adata = load_raw() if args.source == "raw" else load_processed()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(out_path, compression="gzip")
    _summarize(adata, out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
