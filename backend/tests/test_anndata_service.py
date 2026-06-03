# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the AnnData service layer (:mod:`app.services.anndata_service`).

These tests drive the service singleton directly (no HTTP), exercising the
interface declared in ``docs/CONTRACT.md`` section 8:

* :meth:`load` builds a :class:`~app.models.DatasetInfo` with the expected
  embeddings and obs columns.
* :meth:`get_embedding` returns a ``(n, 2)`` float32 array plus bounds.
* :meth:`search_genes`, :meth:`get_expression`, :meth:`get_obs`.
* selection register / resolve and :meth:`selection_stats` (markers, obs
  summary, honest notes).
* :meth:`recluster` / :meth:`recompute_umap` (skipped if ``leidenalg`` /
  ``umap-learn`` are unavailable).

Everything is built on the tiny synthetic dataset from :mod:`tests.conftest`;
no data is ever downloaded. Heavy-compute params are kept deliberately tiny.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.anndata_service import service
from tests.conftest import KNOWN_GENES, N_LEIDEN_CLUSTERS, N_OBS, N_VARS


# --------------------------------------------------------------------------- #
# load -> DatasetInfo
# --------------------------------------------------------------------------- #
def test_load_builds_dataset_info(service_dataset_id: str) -> None:
    """:meth:`load` produces a :class:`DatasetInfo` describing the dataset."""
    info = service.get_info(service_dataset_id)

    assert info.dataset_id == service_dataset_id
    assert info.n_obs == N_OBS
    assert info.n_vars == N_VARS
    assert info.n_genes == N_VARS
    assert info.backed is False
    # The synthetic dataset is small, so it is read fully into memory.
    assert info.path is not None and info.path.endswith(".h5ad")


def test_load_reports_expected_embeddings(service_dataset_id: str) -> None:
    """Embeddings list contains the ``X_*`` obsm keys; default prefers UMAP."""
    info = service.get_info(service_dataset_id)

    assert set(info.embeddings) == {"X_umap", "X_pca"}
    assert info.default_embedding == "X_umap"


def test_load_reports_expected_obs_columns(service_dataset_id: str) -> None:
    """Obs columns classify ``leiden`` categorical and ``total_counts`` continuous."""
    info = service.get_info(service_dataset_id)
    by_name = {col.name: col for col in info.obs_columns}

    assert "leiden" in by_name
    leiden = by_name["leiden"]
    assert leiden.kind == "categorical"
    assert leiden.n_categories == N_LEIDEN_CLUSTERS
    assert leiden.categories is not None
    assert len(leiden.categories) == N_LEIDEN_CLUSTERS

    assert "total_counts" in by_name
    total_counts = by_name["total_counts"]
    assert total_counts.kind == "continuous"
    assert total_counts.min is not None and total_counts.max is not None
    assert total_counts.min <= total_counts.max


# --------------------------------------------------------------------------- #
# get_embedding
# --------------------------------------------------------------------------- #
def test_get_embedding_shape_and_dtype(service_dataset_id: str) -> None:
    """:meth:`get_embedding` returns a C-contiguous float32 ``(n, 2)`` array."""
    coords, bounds = service.get_embedding(service_dataset_id, "X_umap")

    assert coords.shape == (N_OBS, 2)
    assert coords.dtype == np.float32
    assert coords.flags["C_CONTIGUOUS"]
    assert len(bounds) == 4


def test_get_embedding_bounds_match_data(service_dataset_id: str) -> None:
    """Returned bounds equal the per-axis min/max of the coordinates."""
    coords, (min_x, min_y, max_x, max_y) = service.get_embedding(
        service_dataset_id, "X_umap"
    )

    assert min_x == pytest.approx(float(coords[:, 0].min()))
    assert max_x == pytest.approx(float(coords[:, 0].max()))
    assert min_y == pytest.approx(float(coords[:, 1].min()))
    assert max_y == pytest.approx(float(coords[:, 1].max()))


def test_get_embedding_default_key(service_dataset_id: str) -> None:
    """A ``None`` key resolves to the dataset's default embedding (UMAP)."""
    coords, _ = service.get_embedding(service_dataset_id, None)
    assert coords.shape == (N_OBS, 2)


def test_get_embedding_unknown_key_raises(service_dataset_id: str) -> None:
    """An unknown embedding key raises ``KeyError`` (router maps to 404)."""
    with pytest.raises(KeyError):
        service.get_embedding(service_dataset_id, "X_does_not_exist")


# --------------------------------------------------------------------------- #
# search_genes
# --------------------------------------------------------------------------- #
def test_search_genes_prefix_match(service_dataset_id: str) -> None:
    """Searching for ``cd`` finds the known CD* markers (case-insensitive)."""
    hits, total = service.search_genes(service_dataset_id, "cd", limit=50)

    names = {hit.name for hit in hits}
    cd_markers = {g for g in KNOWN_GENES if g.upper().startswith("CD")}
    assert cd_markers.issubset(names)
    assert total >= len(cd_markers)
    # Indices must point back at the right var positions.
    for hit in hits:
        assert 0 <= hit.index < N_VARS


def test_search_genes_empty_query_returns_first_n(service_dataset_id: str) -> None:
    """An empty query returns the first ``limit`` genes and the full total."""
    hits, total = service.search_genes(service_dataset_id, "", limit=5)

    assert len(hits) == 5
    assert total == N_VARS


def test_search_genes_limit_respected(service_dataset_id: str) -> None:
    """The number of returned hits never exceeds ``limit``."""
    hits, _ = service.search_genes(service_dataset_id, "gene", limit=3)
    assert len(hits) <= 3


# --------------------------------------------------------------------------- #
# get_expression
# --------------------------------------------------------------------------- #
def test_get_expression_length_and_dtype(service_dataset_id: str) -> None:
    """:meth:`get_expression` returns a flat float32 vector of length ``n_obs``."""
    values, name, vmin, vmax = service.get_expression(
        service_dataset_id, "CD3D", None
    )

    assert values.shape == (N_OBS,)
    assert values.dtype == np.float32
    assert values.flags["C_CONTIGUOUS"]
    assert name == "CD3D"
    assert vmin <= vmax


def test_get_expression_by_integer_index(service_dataset_id: str) -> None:
    """A gene may be addressed by integer var index in string form."""
    values, name, _, _ = service.get_expression(service_dataset_id, "0", None)

    assert values.shape == (N_OBS,)
    # Var position 0 is the first known marker.
    assert name == KNOWN_GENES[0]


def test_get_expression_unknown_gene_raises(service_dataset_id: str) -> None:
    """An unresolvable gene raises ``KeyError`` (router maps to 404)."""
    with pytest.raises(KeyError):
        service.get_expression(service_dataset_id, "NOT_A_GENE", None)


# --------------------------------------------------------------------------- #
# get_obs
# --------------------------------------------------------------------------- #
def test_get_obs_categorical(service_dataset_id: str) -> None:
    """A categorical column yields int32 codes and ordered category labels."""
    result = service.get_obs(service_dataset_id, "leiden")

    assert result.kind == "categorical"
    assert result.codes is not None
    assert result.codes.dtype == np.int32
    assert result.codes.shape == (N_OBS,)
    assert result.categories is not None
    assert len(result.categories) == N_LEIDEN_CLUSTERS
    # Codes index the category array (no missing values in the synthetic data).
    assert int(result.codes.min()) >= 0
    assert int(result.codes.max()) < N_LEIDEN_CLUSTERS
    assert result.values is None


def test_get_obs_continuous(service_dataset_id: str) -> None:
    """A continuous column yields float32 values with nan-aware min/max."""
    result = service.get_obs(service_dataset_id, "total_counts")

    assert result.kind == "continuous"
    assert result.values is not None
    assert result.values.dtype == np.float32
    assert result.values.shape == (N_OBS,)
    assert result.vmin is not None and result.vmax is not None
    assert result.vmin == pytest.approx(float(np.nanmin(result.values)))
    assert result.vmax == pytest.approx(float(np.nanmax(result.values)))
    assert result.codes is None


def test_get_obs_unknown_column_raises(service_dataset_id: str) -> None:
    """An unknown obs column raises ``KeyError`` (router maps to 404)."""
    with pytest.raises(KeyError):
        service.get_obs(service_dataset_id, "no_such_column")


# --------------------------------------------------------------------------- #
# Selection register / resolve
# --------------------------------------------------------------------------- #
def test_register_and_resolve_selection(service_dataset_id: str) -> None:
    """A registered selection resolves back to the same sorted int32 indices."""
    indices = np.array([5, 10, 10, 2, 7], dtype=np.int32)

    selection_id, n_cells = service.register_selection(service_dataset_id, indices)

    # Deduplicated: {2, 5, 7, 10} -> 4 cells.
    assert isinstance(selection_id, str) and selection_id
    assert n_cells == 4

    resolved = service.resolve_selection(service_dataset_id, selection_id, None)
    assert resolved.dtype == np.int32
    np.testing.assert_array_equal(resolved, np.array([2, 5, 7, 10], dtype=np.int32))


def test_resolve_selection_from_inline_indices(service_dataset_id: str) -> None:
    """Inline indices resolve to a sorted, unique int32 array."""
    resolved = service.resolve_selection(
        service_dataset_id, None, [9, 1, 1, 4]
    )

    assert resolved.dtype == np.int32
    np.testing.assert_array_equal(resolved, np.array([1, 4, 9], dtype=np.int32))


def test_register_selection_out_of_range_raises(service_dataset_id: str) -> None:
    """Out-of-range indices raise ``ValueError`` (router maps to 400)."""
    with pytest.raises(ValueError):
        service.register_selection(
            service_dataset_id, np.array([0, N_OBS], dtype=np.int32)
        )


def test_resolve_selection_unknown_id_raises(service_dataset_id: str) -> None:
    """An unknown selection id raises ``ValueError`` (router maps to 400)."""
    with pytest.raises(ValueError):
        service.resolve_selection(service_dataset_id, "deadbeef", None)


# --------------------------------------------------------------------------- #
# selection_stats
# --------------------------------------------------------------------------- #
def test_selection_stats_markers_and_summary(service_dataset_id: str) -> None:
    """:meth:`selection_stats` returns markers, obs summary, and honest notes."""
    indices = np.arange(0, 60, dtype=np.int32)

    stats = service.selection_stats(
        service_dataset_id, indices, n_markers=10, obs_keys=None
    )

    assert stats.n_cells == 60
    assert stats.fraction == pytest.approx(60 / N_OBS)
    assert stats.rest_cells_used > 0
    assert isinstance(stats.notes, list)

    # Markers: list of MarkerGene with the documented fields.
    assert isinstance(stats.markers, list)
    assert len(stats.markers) <= 10
    assert stats.n_markers == len(stats.markers)
    for marker in stats.markers:
        assert isinstance(marker.name, str) and marker.name
        assert np.isfinite(marker.score)
        assert np.isfinite(marker.log2fc)
        assert 0.0 <= marker.pct_in <= 1.0
        assert 0.0 <= marker.pct_out <= 1.0

    # Obs summary keyed by column name, covering leiden + total_counts.
    assert "leiden" in stats.obs_summary
    assert stats.obs_summary["leiden"].kind == "categorical"
    leiden_summary = stats.obs_summary["leiden"]
    assert leiden_summary.top in leiden_summary.counts
    assert sum(leiden_summary.counts.values()) == 60

    assert "total_counts" in stats.obs_summary
    assert stats.obs_summary["total_counts"].kind == "continuous"
    cont = stats.obs_summary["total_counts"]
    assert cont.min <= cont.mean <= cont.max


def test_selection_stats_respects_obs_keys(service_dataset_id: str) -> None:
    """Explicit ``obs_keys`` restrict the summary to the requested columns."""
    indices = np.arange(0, 40, dtype=np.int32)

    stats = service.selection_stats(
        service_dataset_id, indices, n_markers=5, obs_keys=["leiden"]
    )

    assert set(stats.obs_summary.keys()) == {"leiden"}


# --------------------------------------------------------------------------- #
# recluster / recompute_umap (skipped without leidenalg / umap-learn)
# --------------------------------------------------------------------------- #
def _noop_progress(step: str, frac: float, message: str) -> None:
    """No-op progress callback matching ``Callable[[str, float, str], None]``."""
    return None


def test_recluster_returns_int32_labels(service_dataset_id: str) -> None:
    """:meth:`recluster` returns int32 labels of length ``len(selection)``.

    Skipped when ``leidenalg`` is not importable (no Leiden backend available).
    """
    pytest.importorskip("leidenalg")

    indices = np.arange(0, 120, dtype=np.int32)
    labels, n_clusters = service.recluster(
        service_dataset_id,
        indices,
        {"resolution": 1.0, "n_neighbors": 10, "n_pcs": 5},
        _noop_progress,
    )

    assert labels.dtype == np.int32
    assert labels.shape == (indices.size,)
    assert n_clusters >= 1
    # Cluster codes are dense in [0, n_clusters).
    assert int(labels.min()) >= 0
    assert int(labels.max()) < n_clusters
    assert n_clusters == int(np.unique(labels).size)


def test_recompute_umap_returns_float32_coords(service_dataset_id: str) -> None:
    """:meth:`recompute_umap` returns float32 coords of length ``2 * selection``.

    Skipped when ``umap-learn`` is not importable.
    """
    pytest.importorskip("umap")

    indices = np.arange(0, 80, dtype=np.int32)
    coords, bounds = service.recompute_umap(
        service_dataset_id,
        indices,
        {"n_neighbors": 10, "min_dist": 0.5, "n_pcs": 5},
        _noop_progress,
    )

    assert coords.dtype == np.float32
    assert coords.shape == (2 * indices.size,)
    assert coords.flags["C_CONTIGUOUS"]
    assert len(bounds) == 4
    min_x, min_y, max_x, max_y = bounds
    assert min_x <= max_x
    assert min_y <= max_y
