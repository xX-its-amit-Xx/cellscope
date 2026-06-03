# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared pytest fixtures for the CellScope backend test suite.

All fixtures build a tiny, deterministic synthetic :class:`anndata.AnnData`
object in memory and write it to a temporary ``.h5ad`` file. Nothing here ever
touches the network: the dataset is generated with a fixed NumPy random seed so
that every assertion downstream is reproducible (see ``docs/CONTRACT.md``
section 10, "Tests").

The synthetic dataset deliberately mirrors the structure the rest of the
application expects:

* ``obsm["X_umap"]`` of shape ``(n_obs, 2)`` — the default embedding.
* ``obsm["X_pca"]`` of shape ``(n_obs, n_pcs)`` — so recompute paths can reuse
  existing principal components.
* ``obs["leiden"]`` — a categorical column (string ``Categorical``).
* ``obs["total_counts"]`` — a continuous (float) column.
* ``X`` — a small dense expression matrix with named ``var`` genes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import anndata as ad
import numpy as np
import pytest
from fastapi.testclient import TestClient

# Deterministic dataset geometry. Kept intentionally small so the suite runs in
# a RAM-constrained CI environment without any heavy compute.
SEED: int = 1234
N_OBS: int = 300
N_VARS: int = 50
N_PCS: int = 10
N_LEIDEN_CLUSTERS: int = 4

# A handful of recognisable gene names so the gene-search tests can assert on a
# stable, known prefix ("CD"). The remaining genes are filled with synthetic
# ``GENE####`` names.
KNOWN_GENES: tuple[str, ...] = ("CD3D", "CD3E", "CD8A", "CD4", "CD19", "MS4A1")


def _build_synthetic_adata() -> ad.AnnData:
    """Build the deterministic synthetic :class:`anndata.AnnData` object.

    Returns:
        An in-memory ``AnnData`` with ``N_OBS`` cells and ``N_VARS`` genes, a
        2-D ``X_umap`` embedding, an ``X_pca`` embedding, a categorical
        ``leiden`` obs column, and a continuous ``total_counts`` obs column.
    """
    rng = np.random.default_rng(SEED)

    # Expression matrix: non-negative integer-ish counts cast to float32. Using
    # a Poisson draw keeps values realistic (many zeros) without being huge.
    x = rng.poisson(lam=1.0, size=(N_OBS, N_VARS)).astype(np.float32)
    # Guarantee at least one strongly expressed gene per cell so expression
    # min/max ranges are non-degenerate.
    x[:, 0] = rng.uniform(0.5, 5.0, size=N_OBS).astype(np.float32)

    # Gene (var) names: a few recognisable markers followed by synthetic names.
    var_names = list(KNOWN_GENES) + [
        f"GENE{idx:04d}" for idx in range(N_VARS - len(KNOWN_GENES))
    ]
    assert len(var_names) == N_VARS

    adata = ad.AnnData(X=x)
    adata.var_names = var_names
    adata.var.index.name = "gene_symbol"
    adata.obs_names = [f"cell_{idx:04d}" for idx in range(N_OBS)]

    # 2-D UMAP embedding (the default embedding).
    adata.obsm["X_umap"] = rng.normal(0.0, 5.0, size=(N_OBS, 2)).astype(np.float32)
    # A PCA embedding so recompute paths can reuse existing components.
    adata.obsm["X_pca"] = rng.normal(0.0, 1.0, size=(N_OBS, N_PCS)).astype(np.float32)

    # Categorical obs column: a balanced-ish leiden assignment as strings.
    leiden_codes = rng.integers(0, N_LEIDEN_CLUSTERS, size=N_OBS)
    leiden = np.array([str(code) for code in leiden_codes])
    adata.obs["leiden"] = leiden
    adata.obs["leiden"] = adata.obs["leiden"].astype("category")

    # Continuous obs column: per-cell total counts (sum of the row).
    adata.obs["total_counts"] = np.asarray(x.sum(axis=1), dtype=np.float64)

    return adata


@pytest.fixture(scope="session")
def synthetic_adata() -> ad.AnnData:
    """Session-scoped in-memory synthetic AnnData object.

    Returns:
        The deterministic synthetic dataset; built once per test session.
    """
    return _build_synthetic_adata()


@pytest.fixture(scope="session")
def h5ad_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Write the synthetic AnnData to a temporary ``.h5ad`` file.

    Args:
        tmp_path_factory: pytest factory for session-scoped temporary dirs.

    Returns:
        The absolute path to the written ``.h5ad`` file.
    """
    adata = _build_synthetic_adata()
    out_dir = tmp_path_factory.mktemp("cellscope_data")
    path = out_dir / "synthetic.h5ad"
    adata.write_h5ad(path)
    return path


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """A FastAPI :class:`~fastapi.testclient.TestClient` from ``create_app``.

    Yields:
        A test client wrapping a freshly constructed application instance. The
        ``with`` block ensures startup/shutdown lifespan events run.
    """
    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def loaded_dataset_id(client: TestClient, h5ad_path: Path) -> str:
    """Load the synthetic dataset via the API and return its ``dataset_id``.

    Args:
        client: The FastAPI test client fixture.
        h5ad_path: Path to the synthetic ``.h5ad`` file.

    Returns:
        The ``dataset_id`` (uuid4 hex) assigned to the loaded dataset.
    """
    response = client.post(
        "/api/datasets/load", json={"path": str(h5ad_path)}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    dataset_id = payload["dataset_id"]
    assert isinstance(dataset_id, str) and dataset_id
    return dataset_id


@pytest.fixture()
def service_dataset_id(h5ad_path: Path) -> str:
    """Load the synthetic dataset directly through the service singleton.

    This bypasses the HTTP layer so the service-level tests can exercise
    :mod:`app.services.anndata_service` in isolation.

    Args:
        h5ad_path: Path to the synthetic ``.h5ad`` file.

    Returns:
        The ``dataset_id`` of the loaded dataset.
    """
    from app.services.anndata_service import service

    info = service.load(str(h5ad_path))
    return info.dataset_id
