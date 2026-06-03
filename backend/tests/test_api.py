# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end FastAPI tests using the :class:`~fastapi.testclient.TestClient`.

These tests drive the REST surface described in ``docs/CONTRACT.md`` section 4,
asserting the exact ``X-Cellscope-*`` header names and binary ``byteLength``
invariants from section 9. They never download data: the dataset comes from the
synthetic ``.h5ad`` fixture in :mod:`tests.conftest`.

Binary invariants asserted here:

* Embedding body length ``== 8 * n_obs`` (interleaved xy Float32).
* Expression / continuous-obs body length ``== 4 * n_obs`` (flat Float32).
* Categorical-obs body length ``== 4 * n_obs`` (flat Int32 codes).
* ``X-Cellscope-N-Obs`` is echoed and equals ``byteLength / element_size``.
"""

from __future__ import annotations

import struct

import numpy as np
from fastapi.testclient import TestClient

from tests.conftest import N_LEIDEN_CLUSTERS, N_OBS, N_VARS


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
def test_health(client: TestClient) -> None:
    """``GET /api/health`` reports an ``ok`` status with a version string."""
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str) and body["version"]


# --------------------------------------------------------------------------- #
# Load + metadata
# --------------------------------------------------------------------------- #
def test_load_dataset_returns_dataset_info(loaded_dataset_id: str) -> None:
    """``POST /api/datasets/load`` returns a usable ``dataset_id``."""
    assert isinstance(loaded_dataset_id, str) and loaded_dataset_id


def test_get_dataset_metadata(client: TestClient, loaded_dataset_id: str) -> None:
    """``GET /api/datasets/{id}`` echoes the loaded dataset metadata."""
    response = client.get(f"/api/datasets/{loaded_dataset_id}")

    assert response.status_code == 200, response.text
    info = response.json()
    assert info["dataset_id"] == loaded_dataset_id
    assert info["n_obs"] == N_OBS
    assert info["n_vars"] == N_VARS
    assert info["default_embedding"] == "X_umap"
    assert "X_umap" in info["embeddings"]
    obs_names = {col["name"] for col in info["obs_columns"]}
    assert {"leiden", "total_counts"}.issubset(obs_names)


def test_get_unknown_dataset_404(client: TestClient) -> None:
    """``GET /api/datasets/{id}`` returns 404 for an unknown id."""
    response = client.get("/api/datasets/deadbeefdeadbeef")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Embedding (interleaved xy Float32, 8 * n_obs bytes)
# --------------------------------------------------------------------------- #
def test_embedding_binary_and_headers(
    client: TestClient, loaded_dataset_id: str
) -> None:
    """Embedding body is ``8 * n_obs`` bytes with the documented headers."""
    response = client.get(f"/api/datasets/{loaded_dataset_id}/embedding")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/octet-stream"

    # Exact header names from CONTRACT 4.5.
    assert response.headers["X-Cellscope-Dtype"] == "float32"
    assert response.headers["X-Cellscope-Layout"] == "interleaved-xy"
    assert response.headers["X-Cellscope-Key"] == "X_umap"
    n_obs = int(response.headers["X-Cellscope-N-Obs"])
    assert n_obs == N_OBS

    body = response.content
    assert len(body) == 8 * n_obs  # 2 Float32 per cell

    # Bounds: 4 comma-separated floats.
    bounds = [float(t) for t in response.headers["X-Cellscope-Bounds"].split(",")]
    assert len(bounds) == 4

    # Decode interleaved xy and sanity-check against the bounds.
    coords = np.frombuffer(body, dtype="<f4").reshape(-1, 2)
    assert coords.shape == (n_obs, 2)
    min_x, min_y, max_x, max_y = bounds
    assert float(coords[:, 0].min()) >= min_x - 1e-3
    assert float(coords[:, 0].max()) <= max_x + 1e-3


def test_embedding_explicit_key(client: TestClient, loaded_dataset_id: str) -> None:
    """Requesting an explicit ``key`` echoes that key back in the header."""
    response = client.get(
        f"/api/datasets/{loaded_dataset_id}/embedding", params={"key": "X_pca"}
    )

    assert response.status_code == 200, response.text
    assert response.headers["X-Cellscope-Key"] == "X_pca"
    n_obs = int(response.headers["X-Cellscope-N-Obs"])
    assert len(response.content) == 8 * n_obs


# --------------------------------------------------------------------------- #
# Gene search (JSON)
# --------------------------------------------------------------------------- #
def test_genes_search(client: TestClient, loaded_dataset_id: str) -> None:
    """``GET /api/datasets/{id}/genes`` returns matching hits and a total."""
    response = client.get(
        f"/api/datasets/{loaded_dataset_id}/genes",
        params={"query": "CD3", "limit": 50},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "hits" in body and "total" in body
    names = {hit["name"] for hit in body["hits"]}
    assert {"CD3D", "CD3E"}.issubset(names)
    for hit in body["hits"]:
        assert 0 <= hit["index"] < N_VARS


def test_genes_empty_query_returns_first_n(
    client: TestClient, loaded_dataset_id: str
) -> None:
    """An empty query returns the first ``limit`` genes."""
    response = client.get(
        f"/api/datasets/{loaded_dataset_id}/genes", params={"limit": 5}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["hits"]) == 5
    assert body["total"] == N_VARS


# --------------------------------------------------------------------------- #
# Expression (flat Float32, 4 * n_obs bytes)
# --------------------------------------------------------------------------- #
def test_expression_binary_and_headers(
    client: TestClient, loaded_dataset_id: str
) -> None:
    """Expression body is ``4 * n_obs`` bytes with min/max headers."""
    response = client.get(
        f"/api/datasets/{loaded_dataset_id}/expression", params={"gene": "CD3D"}
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/octet-stream"

    # Exact header names from CONTRACT 4.6.
    assert response.headers["X-Cellscope-Dtype"] == "float32"
    assert response.headers["X-Cellscope-Gene"] == "CD3D"
    assert "X-Cellscope-Min" in response.headers
    assert "X-Cellscope-Max" in response.headers
    n_obs = int(response.headers["X-Cellscope-N-Obs"])
    assert n_obs == N_OBS

    body = response.content
    assert len(body) == 4 * n_obs  # one Float32 per cell

    vmin = float(response.headers["X-Cellscope-Min"])
    vmax = float(response.headers["X-Cellscope-Max"])
    assert vmin <= vmax
    values = np.frombuffer(body, dtype="<f4")
    assert float(values.min()) >= vmin - 1e-3
    assert float(values.max()) <= vmax + 1e-3


def test_expression_unknown_gene_404(
    client: TestClient, loaded_dataset_id: str
) -> None:
    """An unresolvable gene yields HTTP 404."""
    response = client.get(
        f"/api/datasets/{loaded_dataset_id}/expression",
        params={"gene": "NOT_A_GENE"},
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Obs column: categorical (Int32 codes) + continuous (Float32)
# --------------------------------------------------------------------------- #
def test_obs_categorical_binary_and_headers(
    client: TestClient, loaded_dataset_id: str
) -> None:
    """Categorical obs returns Int32 codes of length ``n_obs`` with category count."""
    response = client.get(
        f"/api/datasets/{loaded_dataset_id}/obs", params={"column": "leiden"}
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/octet-stream"

    # Exact header names from CONTRACT 4.6 (categorical).
    assert response.headers["X-Cellscope-Kind"] == "categorical"
    assert response.headers["X-Cellscope-Dtype"] == "int32"
    n_obs = int(response.headers["X-Cellscope-N-Obs"])
    assert n_obs == N_OBS
    assert int(response.headers["X-Cellscope-N-Categories"]) == N_LEIDEN_CLUSTERS

    body = response.content
    assert len(body) == 4 * n_obs  # one Int32 code per cell

    codes = np.frombuffer(body, dtype="<i4")
    assert codes.shape == (n_obs,)
    assert int(codes.min()) >= 0  # no missing values in the synthetic data
    assert int(codes.max()) < N_LEIDEN_CLUSTERS


def test_obs_continuous_binary_and_headers(
    client: TestClient, loaded_dataset_id: str
) -> None:
    """Continuous obs returns Float32 values of length ``n_obs`` with min/max."""
    response = client.get(
        f"/api/datasets/{loaded_dataset_id}/obs",
        params={"column": "total_counts"},
    )

    assert response.status_code == 200, response.text
    assert response.headers["X-Cellscope-Kind"] == "continuous"
    assert response.headers["X-Cellscope-Dtype"] == "float32"
    n_obs = int(response.headers["X-Cellscope-N-Obs"])
    assert n_obs == N_OBS
    assert "X-Cellscope-Min" in response.headers
    assert "X-Cellscope-Max" in response.headers

    body = response.content
    assert len(body) == 4 * n_obs  # one Float32 per cell

    vmin = float(response.headers["X-Cellscope-Min"])
    vmax = float(response.headers["X-Cellscope-Max"])
    values = np.frombuffer(body, dtype="<f4")
    assert float(values.min()) >= vmin - 1e-3
    assert float(values.max()) <= vmax + 1e-3


def test_obs_unknown_column_404(
    client: TestClient, loaded_dataset_id: str
) -> None:
    """An unknown obs column yields HTTP 404."""
    response = client.get(
        f"/api/datasets/{loaded_dataset_id}/obs", params={"column": "nope"}
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Selection: register (binary Int32 body) -> stats (markers)
# --------------------------------------------------------------------------- #
def test_selection_register_binary_then_stats(
    client: TestClient, loaded_dataset_id: str
) -> None:
    """A binary Int32 selection registers, then ``/stats`` returns markers."""
    indices = np.arange(0, 60, dtype="<i4")
    payload = indices.tobytes()
    assert len(payload) == 4 * indices.size

    register = client.post(
        f"/api/datasets/{loaded_dataset_id}/selection",
        content=payload,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert register.status_code == 200, register.text
    ref = register.json()
    selection_id = ref["selection_id"]
    assert isinstance(selection_id, str) and selection_id
    assert ref["n_cells"] == 60

    stats = client.post(
        f"/api/datasets/{loaded_dataset_id}/selection/stats",
        json={"selection_id": selection_id, "n_markers": 10},
    )
    assert stats.status_code == 200, stats.text
    body = stats.json()
    assert body["n_cells"] == 60
    assert isinstance(body["markers"], list)
    assert len(body["markers"]) <= 10
    assert body["n_markers"] == len(body["markers"])
    for marker in body["markers"]:
        assert isinstance(marker["name"], str) and marker["name"]
        assert "score" in marker and "log2fc" in marker
        assert "pval" in marker and "pval_adj" in marker
        assert 0.0 <= marker["pct_in"] <= 1.0
        assert 0.0 <= marker["pct_out"] <= 1.0
    assert "leiden" in body["obs_summary"]
    assert isinstance(body["notes"], list)


def test_selection_register_json_body(
    client: TestClient, loaded_dataset_id: str
) -> None:
    """A JSON ``{"indices": [...]}`` body also registers a selection."""
    response = client.post(
        f"/api/datasets/{loaded_dataset_id}/selection",
        json={"indices": [1, 2, 3, 4, 5]},
    )

    assert response.status_code == 200, response.text
    ref = response.json()
    assert ref["n_cells"] == 5


def test_selection_stats_inline_indices(
    client: TestClient, loaded_dataset_id: str
) -> None:
    """``/stats`` accepts inline ``indices`` without a prior registration."""
    response = client.post(
        f"/api/datasets/{loaded_dataset_id}/selection/stats",
        json={"indices": list(range(0, 50)), "n_markers": 5},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["n_cells"] == 50


def test_selection_register_out_of_range_400(
    client: TestClient, loaded_dataset_id: str
) -> None:
    """Out-of-range indices are rejected with HTTP 400."""
    payload = struct.pack("<2i", 0, N_OBS)  # N_OBS is one past the last valid index
    response = client.post(
        f"/api/datasets/{loaded_dataset_id}/selection",
        content=payload,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 400
