# SPDX-License-Identifier: GPL-3.0-or-later
"""AnnData service layer.

Implements :class:`AnnDataService`, the single owner of all AnnData I/O and
compute for CellScope (see CONTRACT section 8). A module-level singleton
:data:`service` is exported for use by the routers and job manager.

The service keeps an in-memory registry mapping ``dataset_id`` (a ``uuid4().hex``
string) to a :class:`LoadedDataset`. Files at or above
``settings.backed_threshold_mb`` are opened in backed mode (``backed="r"``) so
only ``X``/layers stay on disk while ``obs``/``var``/``obsm`` live in RAM;
smaller files are read fully into memory.

All "heavy read" methods return numpy arrays already cast to the wire dtype
(``float32`` for coordinates/continuous values, ``int32`` for categorical codes
and cell indices) and made C-contiguous, matching the binary protocol
invariants in CONTRACT section 9.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional

import anndata as ad
import numpy as np
import pandas as pd
import scanpy as sc
from scipy import sparse

from app.config import settings
from app.models import (
    CategoricalSummary,
    ContinuousSummary,
    DatasetInfo,
    DatasetSummary,
    GeneHit,
    MarkerGene,
    ObsColumnInfo,
    SelectionStatsResponse,
)

logger = logging.getLogger(__name__)

# Keep scanpy quiet: it otherwise prints to stdout. We rely on the stdlib
# logging module for all diagnostics (CONTRACT section 10: never print).
sc.settings.verbosity = 0
try:  # pragma: no cover - defensive; attribute exists on all supported versions.
    sc.settings.logfile = os.devnull
except Exception:  # noqa: BLE001 - never let logging config break startup.
    logger.debug("Could not redirect scanpy logfile", exc_info=True)

# A progress callback: (step, fraction_in_[0,1], human_message) -> None.
ProgressCallback = Callable[[str, float, str], None]


class NotAnH5adError(ValueError):
    """Raised when a path/upload does not have a ``.h5ad`` extension.

    Subclasses :class:`ValueError` so generic ``ValueError`` handling still
    treats it as a bad request, while routers can map it explicitly to HTTP 415
    (unsupported media type) without inspecting the message string
    (CONTRACT 4.1/4.2).
    """


class UnknownLayerError(ValueError):
    """Raised when an expression request names a layer that does not exist.

    A bad ``layer`` is a request-parameter error (CONTRACT 4.6), so this
    subclasses :class:`ValueError` and routers map it to HTTP 400 — distinct
    from an unknown gene/dataset which remains a ``KeyError`` (HTTP 404).
    """

# Categorical obs columns with more categories than this are still built, but the
# size is acknowledged (CONTRACT/task: always include if <= this, larger is
# acceptable but flagged in logs).
_CATEGORY_SOFT_CAP = 1000


@dataclass
class LoadedDataset:
    """A dataset held in the service registry.

    Attributes:
        adata: The (possibly backed) AnnData object.
        path: Resolved filesystem path the dataset was loaded from, or ``None``.
        backed: ``True`` when ``adata`` was opened with ``backed="r"``.
        info: The cached :class:`DatasetInfo` computed at load time.
    """

    adata: ad.AnnData
    path: Optional[str]
    backed: bool
    info: DatasetInfo


@dataclass
class ObsResult:
    """Internal result of :meth:`AnnDataService.get_obs`.

    Exactly one of ``codes`` / ``values`` is populated depending on ``kind``.

    Attributes:
        kind: ``"categorical"`` or ``"continuous"``.
        codes: ``int32`` codes (length ``n_obs``, ``-1`` for missing) when
            categorical; otherwise ``None``.
        values: ``float32`` values (length ``n_obs``, ``NaN`` allowed) when
            continuous; otherwise ``None``.
        categories: Ordered category labels when categorical; otherwise ``None``.
        vmin: Minimum value for continuous columns; otherwise ``None``.
        vmax: Maximum value for continuous columns; otherwise ``None``.
    """

    kind: str
    codes: Optional[np.ndarray] = None
    values: Optional[np.ndarray] = None
    categories: Optional[list[str]] = None
    vmin: Optional[float] = None
    vmax: Optional[float] = None


def _is_categorical_dtype(series: pd.Series) -> bool:
    """Return whether an obs column should be treated as categorical.

    A column is categorical when it is a pandas ``CategoricalDtype`` or holds
    object / boolean / string values. Everything else (integer, float) is
    treated as continuous.

    Args:
        series: The obs column to classify.

    Returns:
        ``True`` if the column is categorical, ``False`` if continuous.
    """
    dtype = series.dtype
    if isinstance(dtype, pd.CategoricalDtype):
        return True
    if pd.api.types.is_bool_dtype(dtype):
        return True
    if pd.api.types.is_object_dtype(dtype):
        return True
    # pandas "string" extension dtype.
    if pd.api.types.is_string_dtype(dtype) and not pd.api.types.is_numeric_dtype(dtype):
        return True
    return False


def _nan_aware_bounds(arr: np.ndarray) -> tuple[float, float]:
    """Compute ``(min, max)`` of an array ignoring NaNs.

    Args:
        arr: A numeric numpy array (any shape).

    Returns:
        A ``(vmin, vmax)`` tuple of Python floats. If the array is empty or
        all-NaN, returns ``(0.0, 0.0)``.
    """
    if arr.size == 0:
        return 0.0, 0.0
    with np.errstate(all="ignore"):
        vmin = np.nanmin(arr)
        vmax = np.nanmax(arr)
    if not np.isfinite(vmin) or not np.isfinite(vmax):
        return 0.0, 0.0
    return float(vmin), float(vmax)


class AnnDataService:
    """Owns AnnData loading, metadata extraction, reads and heavy compute.

    Thread-safe registry access is guarded by an internal lock; compute methods
    run on worker threads driven by the job manager (CONTRACT section 5). Per
    CONTRACT section 8 this class is instantiated once as the module-level
    :data:`service` singleton.
    """

    def __init__(self) -> None:
        """Initialize an empty registry and selection LRU."""
        self._datasets: dict[str, LoadedDataset] = {}
        # selection_id -> int32 indices, capped LRU per CONTRACT section 2.
        self._selections: "OrderedDict[str, np.ndarray]" = OrderedDict()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # Loading                                                            #
    # ------------------------------------------------------------------ #
    def load(self, path: str) -> DatasetInfo:
        """Load an ``.h5ad`` file and register it.

        The path is resolved relative to ``settings.data_dir`` when not
        absolute. The file must exist and end with ``.h5ad``. Files at or above
        ``settings.backed_threshold_mb`` are opened backed (``backed="r"``),
        otherwise read fully into memory.

        Args:
            path: Absolute path or a path relative to ``settings.data_dir``.

        Returns:
            The computed :class:`DatasetInfo` for the loaded dataset.

        Raises:
            ValueError: If ``path`` is empty.
            NotAnH5adError: If ``path`` does not end with ``.h5ad`` (router maps
                to HTTP 415).
            FileNotFoundError: If the resolved path does not exist.
        """
        if not path or not str(path).strip():
            raise ValueError("path must be a non-empty string")

        resolved = self._resolve_path(path)

        if not resolved.lower().endswith(".h5ad"):
            raise NotAnH5adError(f"Not an .h5ad file: {path}")
        if not os.path.isfile(resolved):
            raise FileNotFoundError(f"No such .h5ad file: {resolved}")

        size_mb = os.path.getsize(resolved) / (1024.0 * 1024.0)
        backed = size_mb >= float(settings.backed_threshold_mb)

        logger.info(
            "Loading AnnData path=%s size_mb=%.1f backed=%s",
            resolved,
            size_mb,
            backed,
        )
        if backed:
            adata = ad.read_h5ad(resolved, backed="r")
        else:
            adata = ad.read_h5ad(resolved)

        return self._register(adata, resolved, backed)

    def load_uploaded(self, filename: str, data: bytes) -> DatasetInfo:
        """Persist uploaded bytes under ``data_dir`` then load them.

        Args:
            filename: The client-supplied filename (basename is used; any
                directory components are stripped for safety).
            data: Raw bytes of the ``.h5ad`` file.

        Returns:
            The computed :class:`DatasetInfo` for the loaded dataset.

        Raises:
            NotAnH5adError: If the filename does not end with ``.h5ad`` (router
                maps to HTTP 415).
        """
        base = os.path.basename((filename or "").strip())
        if not base.lower().endswith(".h5ad"):
            raise NotAnH5adError(f"Uploaded file is not an .h5ad: {filename!r}")

        data_dir = os.path.abspath(settings.data_dir)
        os.makedirs(data_dir, exist_ok=True)
        dest = os.path.join(data_dir, base)
        logger.info("Writing uploaded file %s (%d bytes)", dest, len(data))
        with open(dest, "wb") as handle:
            handle.write(data)
        return self.load(dest)

    def get_info(self, dataset_id: str) -> DatasetInfo:
        """Return the cached :class:`DatasetInfo` for a dataset.

        Args:
            dataset_id: The registry id.

        Returns:
            The dataset's :class:`DatasetInfo`.

        Raises:
            KeyError: If the dataset id is unknown (router maps to HTTP 404).
        """
        return self._require(dataset_id).info

    def list_loaded(self) -> list[DatasetSummary]:
        """Summarize every currently loaded dataset.

        Returns:
            A list of :class:`DatasetSummary`, one per registered dataset.
        """
        with self._lock:
            entries = list(self._datasets.items())
        summaries: list[DatasetSummary] = []
        for dataset_id, loaded in entries:
            info = loaded.info
            summaries.append(
                DatasetSummary(
                    dataset_id=dataset_id,
                    path=info.path,
                    n_obs=info.n_obs,
                    n_vars=info.n_vars,
                )
            )
        return summaries

    def available_files(self) -> list[str]:
        """List ``.h5ad`` files discoverable under ``settings.data_dir``.

        Returns:
            Sorted basenames of ``.h5ad`` files found in the data directory
            (not necessarily loaded). Empty if the directory does not exist.
        """
        data_dir = os.path.abspath(settings.data_dir)
        if not os.path.isdir(data_dir):
            return []
        files = [
            name
            for name in os.listdir(data_dir)
            if name.lower().endswith(".h5ad")
            and os.path.isfile(os.path.join(data_dir, name))
        ]
        return sorted(files)

    # ------------------------------------------------------------------ #
    # Embedding                                                          #
    # ------------------------------------------------------------------ #
    def get_embedding(
        self, dataset_id: str, key: Optional[str]
    ) -> tuple[np.ndarray, tuple[float, float, float, float]]:
        """Return interleaved-xy embedding coordinates and their bounds.

        Args:
            dataset_id: The registry id.
            key: An ``obsm`` key (e.g. ``"X_umap"``). ``None`` uses the
                dataset's ``default_embedding``.

        Returns:
            A tuple ``(coords, bounds)`` where ``coords`` is a C-contiguous
            ``float32`` array of shape ``(n_obs, 2)`` and ``bounds`` is
            ``(min_x, min_y, max_x, max_y)``.

        Raises:
            KeyError: If the dataset id is unknown, no embedding exists, or the
                requested key is missing.
        """
        loaded = self._require(dataset_id)
        resolved_key = key or loaded.info.default_embedding
        if resolved_key is None:
            raise KeyError("dataset has no embedding")
        if resolved_key not in loaded.adata.obsm:
            raise KeyError(f"unknown embedding key: {resolved_key}")

        raw = np.asarray(loaded.adata.obsm[resolved_key])
        if raw.ndim != 2 or raw.shape[1] < 2:
            raise KeyError(f"embedding {resolved_key} has fewer than 2 columns")
        coords = np.ascontiguousarray(raw[:, :2], dtype=np.float32)

        min_x, max_x = _nan_aware_bounds(coords[:, 0])
        min_y, max_y = _nan_aware_bounds(coords[:, 1])
        bounds = (min_x, min_y, max_x, max_y)
        return coords, bounds

    # ------------------------------------------------------------------ #
    # Gene search & expression                                           #
    # ------------------------------------------------------------------ #
    def search_genes(
        self, dataset_id: str, query: str, limit: int
    ) -> tuple[list[GeneHit], int]:
        """Search var names case-insensitively, prefix matches ranked first.

        Args:
            dataset_id: The registry id.
            query: The search string. Empty returns the first ``limit`` genes.
            limit: Maximum number of hits to return.

        Returns:
            A tuple ``(hits, total_matches)`` where ``hits`` is at most
            ``limit`` :class:`GeneHit` entries and ``total_matches`` is the
            total number of matching genes (before truncation).

        Raises:
            KeyError: If the dataset id is unknown.
        """
        loaded = self._require(dataset_id)
        var_names = loaded.adata.var_names
        limit = max(0, int(limit))

        if not query:
            total = len(var_names)
            hits = [
                GeneHit(name=str(var_names[i]), index=int(i))
                for i in range(min(limit, total))
            ]
            return hits, total

        needle = query.lower()
        prefix: list[tuple[int, str]] = []
        substring: list[tuple[int, str]] = []
        for idx, name in enumerate(var_names):
            lowered = str(name).lower()
            pos = lowered.find(needle)
            if pos == 0:
                prefix.append((idx, str(name)))
            elif pos > 0:
                substring.append((idx, str(name)))

        ordered = prefix + substring
        total = len(ordered)
        hits = [GeneHit(name=name, index=idx) for idx, name in ordered[:limit]]
        return hits, total

    def get_expression(
        self, dataset_id: str, gene: str, layer: Optional[str]
    ) -> tuple[np.ndarray, str, float, float]:
        """Read a single gene's expression vector across all cells.

        Resolves ``gene`` to a var position by name first, then by integer
        index (string form). Reads exactly one column from ``X`` (or the named
        layer), densifying if sparse.

        Note:
            In backed mode this performs a single-column read against an
            on-disk CSR matrix, which can be slow because CSR is row-major;
            this is the only disk-bound read path in the API.

        Args:
            dataset_id: The registry id.
            gene: A var name or an integer var index in string form.
            layer: ``None`` / ``"X"`` reads ``adata.X``; otherwise reads
                ``adata.layers[layer]``.

        Returns:
            A tuple ``(values, resolved_name, vmin, vmax)`` where ``values`` is
            a flat C-contiguous ``float32`` array of length ``n_obs``.

        Raises:
            KeyError: If the dataset id is unknown or the gene cannot be
                resolved (router maps to HTTP 404).
            UnknownLayerError: If the named layer is missing; ``layer`` is a
                request parameter, so the router maps this to HTTP 400.
        """
        loaded = self._require(dataset_id)
        adata = loaded.adata
        var_idx, resolved_name = self._resolve_gene(adata, gene)

        use_layer = layer if (layer and layer != "X") else None
        if use_layer is not None and use_layer not in adata.layers:
            raise UnknownLayerError(f"unknown layer: {use_layer}")

        # Slice a single column. This works in backed mode (returns an in-memory
        # view of one column) and in-memory alike.
        sub = adata[:, var_idx]
        col = sub.layers[use_layer] if use_layer is not None else sub.X
        if sparse.issparse(col):
            col = col.toarray()
        values = np.ascontiguousarray(np.asarray(col).reshape(-1), dtype=np.float32)

        vmin, vmax = _nan_aware_bounds(values)
        return values, resolved_name, vmin, vmax

    # ------------------------------------------------------------------ #
    # Obs columns                                                        #
    # ------------------------------------------------------------------ #
    def get_obs(self, dataset_id: str, column: str) -> ObsResult:
        """Return categorical codes or continuous values for an obs column.

        Categorical columns yield ``int32`` codes (``-1`` for missing) plus the
        ordered category labels. Continuous columns yield ``float32`` values
        (``NaN`` allowed) plus nan-aware ``vmin``/``vmax``.

        Args:
            dataset_id: The registry id.
            column: The ``adata.obs`` column name.

        Returns:
            An :class:`ObsResult` describing the column.

        Raises:
            KeyError: If the dataset id is unknown or the column does not exist.
        """
        loaded = self._require(dataset_id)
        if column not in loaded.adata.obs.columns:
            raise KeyError(f"unknown obs column: {column}")
        series = loaded.adata.obs[column]

        if _is_categorical_dtype(series):
            cat = series.astype("category")
            categories = [str(c) for c in cat.cat.categories]
            codes = np.ascontiguousarray(cat.cat.codes.to_numpy(), dtype=np.int32)
            return ObsResult(kind="categorical", codes=codes, categories=categories)

        values = np.ascontiguousarray(
            pd.to_numeric(series, errors="coerce").to_numpy(), dtype=np.float32
        )
        vmin, vmax = _nan_aware_bounds(values)
        return ObsResult(kind="continuous", values=values, vmin=vmin, vmax=vmax)

    # ------------------------------------------------------------------ #
    # Selections                                                         #
    # ------------------------------------------------------------------ #
    def register_selection(
        self, dataset_id: str, indices: np.ndarray
    ) -> tuple[str, int]:
        """Validate and store a set of cell indices, returning a handle.

        Args:
            dataset_id: The registry id.
            indices: Cell indices into the full dataset. Coerced to ``int32``,
                deduplicated and sorted before storage.

        Returns:
            A tuple ``(selection_id, n_cells)``.

        Raises:
            KeyError: If the dataset id is unknown.
            ValueError: If any index is outside ``[0, n_obs)``.
        """
        loaded = self._require(dataset_id)
        n_obs = loaded.adata.n_obs
        idx = np.asarray(indices).reshape(-1)
        if idx.size == 0:
            raise ValueError("selection is empty")
        idx = np.unique(idx.astype(np.int64, copy=False))
        if idx[0] < 0 or idx[-1] >= n_obs:
            raise ValueError(
                f"selection indices out of range [0, {n_obs}): "
                f"min={int(idx[0])} max={int(idx[-1])}"
            )
        stored = np.ascontiguousarray(idx, dtype=np.int32)

        selection_id = uuid.uuid4().hex
        with self._lock:
            self._selections[selection_id] = stored
            self._selections.move_to_end(selection_id)
            while len(self._selections) > max(1, int(settings.selection_lru)):
                evicted, _ = self._selections.popitem(last=False)
                logger.debug("Evicted selection %s from LRU", evicted)
        logger.info(
            "Registered selection %s for dataset %s (%d cells)",
            selection_id,
            dataset_id,
            stored.size,
        )
        return selection_id, int(stored.size)

    def resolve_selection(
        self,
        dataset_id: str,
        selection_id: Optional[str],
        indices: Optional[list[int]],
    ) -> np.ndarray:
        """Resolve a selection to a sorted, unique ``int32`` index array.

        Exactly one of ``selection_id`` / ``indices`` should be supplied;
        ``selection_id`` takes precedence when both are present.

        Args:
            dataset_id: The registry id.
            selection_id: A handle previously returned by
                :meth:`register_selection`.
            indices: Explicit indices, used when ``selection_id`` is ``None``.

        Returns:
            A C-contiguous ``int32`` array of unique, sorted indices, all in
            ``[0, n_obs)``.

        Raises:
            KeyError: If the dataset id is unknown.
            ValueError: If neither input is provided, the selection id is
                unknown/expired, or any index is out of range.
        """
        loaded = self._require(dataset_id)
        n_obs = loaded.adata.n_obs

        if selection_id is not None:
            with self._lock:
                stored = self._selections.get(selection_id)
                if stored is not None:
                    self._selections.move_to_end(selection_id)
            if stored is None:
                raise ValueError(f"unknown or expired selection_id: {selection_id}")
            return np.ascontiguousarray(stored, dtype=np.int32)

        if indices is None:
            raise ValueError("either selection_id or indices must be provided")
        idx = np.asarray(indices).reshape(-1)
        if idx.size == 0:
            raise ValueError("selection is empty")
        idx = np.unique(idx.astype(np.int64, copy=False))
        if idx[0] < 0 or idx[-1] >= n_obs:
            raise ValueError(
                f"selection indices out of range [0, {n_obs}): "
                f"min={int(idx[0])} max={int(idx[-1])}"
            )
        return np.ascontiguousarray(idx, dtype=np.int32)

    # ------------------------------------------------------------------ #
    # Selection statistics / markers                                     #
    # ------------------------------------------------------------------ #
    def selection_stats(
        self,
        dataset_id: str,
        indices: np.ndarray,
        n_markers: int,
        obs_keys: Optional[list[str]],
    ) -> SelectionStatsResponse:
        """Compute marker genes and obs summaries for a selection.

        Builds a labeled in-memory AnnData containing the selection plus a
        seeded random sample of the rest (capped at ``settings.marker_rest_cap``
        for determinism and memory), then runs
        ``scanpy.tl.rank_genes_groups`` (``method="wilcoxon"``) comparing the
        ``"selection"`` group against the ``"rest"`` group.

        If ``X`` looks like raw counts (large max, integer-like) the combined
        matrix is normalized (``normalize_total`` + ``log1p``) on a copy before
        ranking; this is recorded honestly in ``notes``.

        Args:
            dataset_id: The registry id.
            indices: ``int32`` (or coercible) selection indices into the full
                dataset.
            n_markers: Number of top markers to return per CONTRACT default 25.
            obs_keys: Obs columns to summarize. ``None`` selects all categorical
                columns with at most 50 categories plus all numeric columns.

        Returns:
            A populated :class:`SelectionStatsResponse`.

        Raises:
            KeyError: If the dataset id is unknown.
            ValueError: If the selection is empty.
        """
        loaded = self._require(dataset_id)
        adata = loaded.adata
        n_obs = adata.n_obs

        sel_idx = np.unique(np.asarray(indices).reshape(-1).astype(np.int64, copy=False))
        if sel_idx.size == 0:
            raise ValueError("selection is empty")

        notes: list[str] = []
        rng = np.random.default_rng(0)

        # Build the "rest" index set, subsampled deterministically if needed.
        rest_mask = np.ones(n_obs, dtype=bool)
        rest_mask[sel_idx] = False
        rest_all = np.flatnonzero(rest_mask)
        cap = max(0, int(settings.marker_rest_cap))
        if rest_all.size > cap:
            rest_idx = np.sort(rng.choice(rest_all, size=cap, replace=False))
            notes.append(
                f"rest subsampled to {cap} of {int(rest_all.size)} cells "
                f"(seed=0) for marker ranking"
            )
        else:
            rest_idx = rest_all
        rest_cells_used = int(rest_idx.size)

        n_cells = int(sel_idx.size)
        fraction = float(n_cells / n_obs) if n_obs else 0.0

        markers: list[MarkerGene] = []
        if rest_cells_used == 0:
            notes.append("no rest cells available; marker ranking skipped")
        else:
            markers = self._compute_markers(
                adata, sel_idx, rest_idx, int(n_markers), notes
            )

        obs_summary = self._build_obs_summary(adata, sel_idx, obs_keys)

        return SelectionStatsResponse(
            n_cells=n_cells,
            fraction=fraction,
            n_markers=len(markers),
            rest_cells_used=rest_cells_used,
            markers=markers,
            obs_summary=obs_summary,
            notes=notes,
        )

    def _compute_markers(
        self,
        adata: ad.AnnData,
        sel_idx: np.ndarray,
        rest_idx: np.ndarray,
        n_markers: int,
        notes: list[str],
    ) -> list[MarkerGene]:
        """Run ``rank_genes_groups`` on a selection-vs-rest in-memory AnnData.

        Args:
            adata: The (possibly backed) source AnnData.
            sel_idx: Sorted unique selection indices.
            rest_idx: Sorted unique sampled rest indices.
            n_markers: Number of top markers to extract.
            notes: Mutable list appended with honest caveats.

        Returns:
            A list of :class:`MarkerGene` ordered by ranking score.
        """
        # Order: selection rows first, then rest. Labels follow the same order.
        combined_idx = np.concatenate([sel_idx, rest_idx])
        subset = self._subset_to_memory(adata, combined_idx)

        labels = np.array(
            ["selection"] * sel_idx.size + ["rest"] * rest_idx.size, dtype=object
        )
        subset.obs["_cellscope_group"] = pd.Categorical(
            labels, categories=["selection", "rest"]
        )

        # Capture the positive-value (expressing) mask from the RAW subset
        # matrix before any normalize_total/log1p. CONTRACT 6.5 defines
        # pct_in/pct_out on raw expression, so this must precede the work copy.
        raw_positive = self._positive_mask(subset.X)

        work = subset
        if self._looks_like_counts(subset.X):
            notes.append(
                "X looked like raw counts; normalize_total + log1p applied to a "
                "copy before marker ranking"
            )
            work = subset.copy()
            sc.pp.normalize_total(work, target_sum=1e4)
            sc.pp.log1p(work)

        try:
            sc.tl.rank_genes_groups(
                work,
                groupby="_cellscope_group",
                groups=["selection"],
                reference="rest",
                method="wilcoxon",
                n_genes=int(max(1, n_markers)),
            )
        except Exception:  # noqa: BLE001 - surface as an honest note, not a crash.
            logger.exception("rank_genes_groups failed")
            notes.append("marker ranking failed; no markers returned")
            return []

        result = work.uns["rank_genes_groups"]
        names = np.asarray(result["names"]["selection"])
        scores = np.asarray(result["scores"]["selection"], dtype=np.float64)
        lfc = np.asarray(result["logfoldchanges"]["selection"], dtype=np.float64)
        pvals = np.asarray(result["pvals"]["selection"], dtype=np.float64)
        pvals_adj = np.asarray(result["pvals_adj"]["selection"], dtype=np.float64)

        requested = int(max(1, n_markers))
        top = min(requested, names.size)
        gene_names = [str(names[i]) for i in range(top)]
        if names.size < requested:
            notes.append(
                f"requested {requested} markers but only {int(names.size)} genes "
                f"available; returning {top}"
            )

        # Resolve the selected gene names to var positions in the subset so the
        # raw expressing mask (computed on the raw matrix) can be indexed.
        var_pos = work.var_names.get_indexer(gene_names)
        pct_in, pct_out = self._fraction_expressing(
            raw_positive, var_pos, sel_idx.size
        )

        markers: list[MarkerGene] = []
        for i, name in enumerate(gene_names):
            markers.append(
                MarkerGene(
                    name=name,
                    score=_finite(scores[i]),
                    log2fc=_finite(lfc[i]),
                    pval=_finite(pvals[i], default=1.0),
                    pval_adj=_finite(pvals_adj[i], default=1.0),
                    pct_in=pct_in[i],
                    pct_out=pct_out[i],
                )
            )
        return markers

    @staticmethod
    def _positive_mask(matrix: object) -> np.ndarray:
        """Return a dense boolean ``(n_obs, n_vars)`` mask of expressing entries.

        The mask records ``X > 0`` on the supplied matrix (the RAW subset, before
        any normalize_total/log1p), used to compute pct_in/pct_out per CONTRACT
        6.5 which defines those fractions on raw expression.

        Args:
            matrix: A dense or sparse expression matrix.

        Returns:
            A boolean numpy array with ``True`` where the value is ``> 0``.
        """
        if sparse.issparse(matrix):
            return np.asarray((matrix > 0).todense())
        return np.asarray(matrix) > 0

    @staticmethod
    def _fraction_expressing(
        raw_positive: np.ndarray, var_pos: np.ndarray, n_selection: int
    ) -> tuple[list[float], list[float]]:
        """Compute the fraction of cells expressing (>0) each gene per group.

        Operates on the RAW expressing mask (``X > 0`` from the un-normalized
        subset), so the fractions reflect raw expression per CONTRACT 6.5. Rows
        are ordered selection-first (``n_selection`` of them) then rest.

        Args:
            raw_positive: Boolean ``(n_obs, n_vars)`` mask from the raw subset.
            var_pos: Var positions of the selected genes, in output order.
            n_selection: Number of leading rows belonging to the selection.

        Returns:
            A tuple ``(pct_in, pct_out)`` of per-gene fractions in ``[0, 1]``.
        """
        pct_in: list[float] = []
        pct_out: list[float] = []
        n_total = int(raw_positive.shape[0])
        n_rest = n_total - n_selection
        for pos in var_pos:
            col = raw_positive[:, int(pos)]
            sel_part = col[:n_selection]
            rest_part = col[n_selection:]
            pin = float(np.count_nonzero(sel_part) / n_selection) if n_selection else 0.0
            pout = float(np.count_nonzero(rest_part) / n_rest) if n_rest else 0.0
            pct_in.append(pin)
            pct_out.append(pout)
        return pct_in, pct_out

    def _build_obs_summary(
        self,
        adata: ad.AnnData,
        sel_idx: np.ndarray,
        obs_keys: Optional[list[str]],
    ) -> dict[str, object]:
        """Summarize requested obs columns over the selection.

        Args:
            adata: The source AnnData (obs is always in RAM, even backed).
            sel_idx: Sorted unique selection indices.
            obs_keys: Columns to summarize, or ``None`` for the default set
                (categorical columns with at most 50 categories plus numeric
                columns).

        Returns:
            A mapping of column name to :class:`CategoricalSummary` or
            :class:`ContinuousSummary`.
        """
        obs = adata.obs
        if obs_keys is None:
            keys: list[str] = []
            for col in obs.columns:
                series = obs[col]
                if _is_categorical_dtype(series):
                    n_cat = int(series.astype("category").cat.categories.size)
                    if n_cat <= 50:
                        keys.append(str(col))
                elif pd.api.types.is_numeric_dtype(series.dtype):
                    keys.append(str(col))
        else:
            keys = [k for k in obs_keys if k in obs.columns]

        summary: dict[str, object] = {}
        sel_obs = obs.iloc[sel_idx]
        for col in keys:
            series = sel_obs[col]
            if _is_categorical_dtype(series):
                # Only labels actually present in the selection are counted;
                # drop zero-count categories carried over from the full dataset's
                # categorical dtype (CONTRACT 6.5: counts cover labels within the
                # selection). "top" is recomputed from the filtered counts.
                counts_series = series.astype("category").value_counts()
                counts = {
                    str(label): int(cnt)
                    for label, cnt in counts_series.items()
                    if int(cnt) > 0
                }
                top = max(counts, key=counts.get) if counts else ""
                summary[col] = CategoricalSummary(
                    kind="categorical", counts=counts, top=top
                )
            else:
                values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=np.float64)
                vmin, vmax = _nan_aware_bounds(values)
                with np.errstate(all="ignore"):
                    mean = float(np.nanmean(values)) if values.size else 0.0
                    median = float(np.nanmedian(values)) if values.size else 0.0
                    std = float(np.nanstd(values)) if values.size else 0.0
                summary[col] = ContinuousSummary(
                    kind="continuous",
                    mean=_finite(mean),
                    median=_finite(median),
                    min=vmin,
                    max=vmax,
                    std=_finite(std),
                )
        return summary

    # ------------------------------------------------------------------ #
    # Recompute: clustering & UMAP                                       #
    # ------------------------------------------------------------------ #
    def recluster(
        self,
        dataset_id: str,
        indices: np.ndarray,
        params: dict,
        progress: ProgressCallback,
    ) -> tuple[np.ndarray, int]:
        """Re-cluster a selection with Leiden, reporting progress.

        Steps (matching CONTRACT section 5 ``step`` labels): ``subset``,
        ``pca``, ``neighbors``, ``leiden``, ``finalize``. Existing ``X_pca``
        rows are reused when present, otherwise PCA is computed.

        Args:
            dataset_id: The registry id.
            indices: Selection indices into the full dataset.
            params: ``{resolution?, n_neighbors?, n_pcs?}``.
            progress: Callback ``(step, fraction, message)``.

        Returns:
            A tuple ``(labels, n_clusters)`` where ``labels`` is a C-contiguous
            ``int32`` array of length ``len(indices)`` (cluster per selected
            cell, in selection order).

        Raises:
            KeyError: If the dataset id is unknown.
            ValueError: If the selection is empty.
        """
        loaded = self._require(dataset_id)
        sel_idx = self._prepare_compute_indices(indices)

        progress("subset", 0.05, "Subsetting selection into memory")
        subset = self._subset_to_memory(loaded.adata, sel_idx)

        n_pcs = self._ensure_pca(loaded.adata, subset, sel_idx, params, progress)

        n_neighbors = int(params.get("n_neighbors", 15) or 15)
        progress("neighbors", 0.5, "Building kNN graph")
        sc.pp.neighbors(subset, n_neighbors=n_neighbors, n_pcs=n_pcs)

        resolution = float(params.get("resolution", 1.0) or 1.0)
        progress("leiden", 0.75, "Running Leiden clustering")
        self._run_leiden(subset, resolution)

        progress("finalize", 0.95, "Finalizing cluster labels")
        cats = subset.obs["leiden"].astype("category")
        labels = np.ascontiguousarray(cats.cat.codes.to_numpy(), dtype=np.int32)
        n_clusters = int(cats.cat.categories.size)
        progress("finalize", 1.0, f"Found {n_clusters} clusters")
        logger.info(
            "recluster dataset=%s n_sel=%d n_clusters=%d",
            dataset_id,
            sel_idx.size,
            n_clusters,
        )
        return labels, n_clusters

    def recompute_umap(
        self,
        dataset_id: str,
        indices: np.ndarray,
        params: dict,
        progress: ProgressCallback,
    ) -> tuple[np.ndarray, tuple[float, float, float, float]]:
        """Recompute a UMAP embedding for a selection, reporting progress.

        Steps: ``subset``, ``pca``, ``neighbors``, ``umap``, ``finalize``.

        Args:
            dataset_id: The registry id.
            indices: Selection indices into the full dataset.
            params: ``{n_neighbors?, min_dist?, n_pcs?}``.
            progress: Callback ``(step, fraction, message)``.

        Returns:
            A tuple ``(coords, bounds)`` where ``coords`` is a C-contiguous
            ``float32`` array of length ``2 * len(indices)`` holding interleaved
            xy of the new ``X_umap`` and ``bounds`` is
            ``(min_x, min_y, max_x, max_y)``.

        Raises:
            KeyError: If the dataset id is unknown.
            ValueError: If the selection is empty.
        """
        loaded = self._require(dataset_id)
        sel_idx = self._prepare_compute_indices(indices)

        progress("subset", 0.05, "Subsetting selection into memory")
        subset = self._subset_to_memory(loaded.adata, sel_idx)

        n_pcs = self._ensure_pca(loaded.adata, subset, sel_idx, params, progress)

        n_neighbors = int(params.get("n_neighbors", 15) or 15)
        progress("neighbors", 0.45, "Building kNN graph")
        sc.pp.neighbors(subset, n_neighbors=n_neighbors, n_pcs=n_pcs)

        min_dist = float(params.get("min_dist", 0.5) or 0.5)
        progress("umap", 0.7, "Computing UMAP layout")
        sc.tl.umap(subset, min_dist=min_dist)

        progress("finalize", 0.95, "Finalizing coordinates")
        raw = np.asarray(subset.obsm["X_umap"])[:, :2]
        coords = np.ascontiguousarray(raw, dtype=np.float32)
        min_x, max_x = _nan_aware_bounds(coords[:, 0])
        min_y, max_y = _nan_aware_bounds(coords[:, 1])
        bounds = (min_x, min_y, max_x, max_y)
        flat = np.ascontiguousarray(coords.reshape(-1), dtype=np.float32)
        progress("finalize", 1.0, "UMAP complete")
        logger.info(
            "recompute_umap dataset=%s n_sel=%d", dataset_id, sel_idx.size
        )
        return flat, bounds

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #
    def _register(
        self, adata: ad.AnnData, path: Optional[str], backed: bool
    ) -> DatasetInfo:
        """Build :class:`DatasetInfo`, register the dataset and return the info.

        Args:
            adata: The loaded AnnData object.
            path: Resolved filesystem path or ``None``.
            backed: Whether ``adata`` is backed.

        Returns:
            The freshly built :class:`DatasetInfo`.
        """
        dataset_id = uuid.uuid4().hex

        embeddings = [
            key
            for key in adata.obsm.keys()
            if key.startswith("X_") and np.asarray(adata.obsm[key]).ndim == 2
            and np.asarray(adata.obsm[key]).shape[1] >= 2
        ]
        default_embedding = self._pick_default_embedding(embeddings)
        obs_columns = self._build_obs_columns(adata)

        info = DatasetInfo(
            dataset_id=dataset_id,
            path=path,
            n_obs=int(adata.n_obs),
            n_vars=int(adata.n_vars),
            backed=backed,
            embeddings=embeddings,
            default_embedding=default_embedding,
            obs_columns=obs_columns,
            var_index_name=adata.var.index.name,
            n_genes=int(adata.n_vars),
        )
        with self._lock:
            self._datasets[dataset_id] = LoadedDataset(
                adata=adata, path=path, backed=backed, info=info
            )
        logger.info(
            "Registered dataset %s n_obs=%d n_vars=%d embeddings=%s",
            dataset_id,
            info.n_obs,
            info.n_vars,
            embeddings,
        )
        return info

    @staticmethod
    def _pick_default_embedding(embeddings: list[str]) -> Optional[str]:
        """Choose the preferred default embedding key.

        Preference order: ``X_umap`` then ``X_tsne`` then ``X_pca`` then the
        first available ``X_*`` key.

        Args:
            embeddings: Available ``X_*`` obsm keys with at least 2 columns.

        Returns:
            The chosen key, or ``None`` when no embeddings exist.
        """
        for preferred in ("X_umap", "X_tsne", "X_pca"):
            if preferred in embeddings:
                return preferred
        return embeddings[0] if embeddings else None

    @staticmethod
    def _build_obs_columns(adata: ad.AnnData) -> list[ObsColumnInfo]:
        """Classify every obs column as categorical or continuous.

        Categorical columns expose their ordered category labels (capped only
        in spirit: lists of up to ``_CATEGORY_SOFT_CAP`` are always included,
        larger ones are still built but logged as large). Continuous columns
        expose nan-aware ``min``/``max``.

        Args:
            adata: The loaded AnnData object.

        Returns:
            A list of :class:`ObsColumnInfo`, one per obs column.
        """
        columns: list[ObsColumnInfo] = []
        for name in adata.obs.columns:
            series = adata.obs[name]
            if _is_categorical_dtype(series):
                cat = series.astype("category")
                categories = [str(c) for c in cat.cat.categories]
                n_categories = len(categories)
                if n_categories > _CATEGORY_SOFT_CAP:
                    logger.warning(
                        "obs column %s has %d categories (> %d); large payload",
                        name,
                        n_categories,
                        _CATEGORY_SOFT_CAP,
                    )
                columns.append(
                    ObsColumnInfo(
                        name=str(name),
                        kind="categorical",
                        n_categories=n_categories,
                        categories=categories,
                        min=None,
                        max=None,
                    )
                )
            else:
                values = pd.to_numeric(series, errors="coerce").to_numpy(
                    dtype=np.float64
                )
                vmin, vmax = _nan_aware_bounds(values)
                columns.append(
                    ObsColumnInfo(
                        name=str(name),
                        kind="continuous",
                        n_categories=None,
                        categories=None,
                        min=vmin,
                        max=vmax,
                    )
                )
        return columns

    @staticmethod
    def _resolve_path(path: str) -> str:
        """Resolve ``path`` relative to ``settings.data_dir`` if not absolute.

        Args:
            path: An absolute path or one relative to the data directory.

        Returns:
            The absolute, normalized filesystem path.
        """
        if os.path.isabs(path):
            return os.path.abspath(path)
        return os.path.abspath(os.path.join(settings.data_dir, path))

    def _require(self, dataset_id: str) -> LoadedDataset:
        """Fetch a registered dataset or raise ``KeyError``.

        Args:
            dataset_id: The registry id.

        Returns:
            The :class:`LoadedDataset`.

        Raises:
            KeyError: If the dataset id is unknown (router maps to HTTP 404).
        """
        with self._lock:
            loaded = self._datasets.get(dataset_id)
        if loaded is None:
            raise KeyError(f"unknown dataset_id: {dataset_id}")
        return loaded

    @staticmethod
    def _resolve_gene(adata: ad.AnnData, gene: str) -> tuple[int, str]:
        """Resolve a gene reference to ``(var_position, resolved_var_name)``.

        Tries the var name first, then an integer index given in string form.

        Args:
            adata: The AnnData object.
            gene: A var name or integer var index as a string.

        Returns:
            A tuple ``(position, resolved_name)``.

        Raises:
            KeyError: If the gene cannot be resolved.
        """
        var_names = adata.var_names
        token = str(gene)
        # Try by name (use the index for O(1) lookup where possible).
        loc = var_names.get_indexer([token])
        if loc[0] != -1:
            pos = int(loc[0])
            return pos, str(var_names[pos])
        # Try by integer index in string form.
        stripped = token.strip()
        if stripped.lstrip("+-").isdigit():
            pos = int(stripped)
            if 0 <= pos < adata.n_vars:
                return pos, str(var_names[pos])
        raise KeyError(f"gene not found: {gene}")

    @staticmethod
    def _subset_to_memory(adata: ad.AnnData, idx: np.ndarray) -> ad.AnnData:
        """Return an in-memory AnnData for the given row indices.

        For backed datasets the subset is materialized via ``.to_memory()``;
        for in-memory datasets a defensive copy is taken so downstream compute
        (which mutates ``obs``/``uns``) never touches the registered object.

        Args:
            adata: The (possibly backed) source AnnData.
            idx: Row indices to keep (order preserved).

        Returns:
            An in-memory AnnData containing the selected rows.
        """
        sub = adata[idx]
        if getattr(adata, "isbacked", False):
            return sub.to_memory()
        return sub.copy()

    def _prepare_compute_indices(self, indices: np.ndarray) -> np.ndarray:
        """Coerce and validate selection indices for a compute job.

        Args:
            indices: Selection indices into the full dataset.

        Returns:
            An ``int64`` array of indices (order preserved, used to subset).

        Raises:
            ValueError: If the selection is empty.
        """
        idx = np.asarray(indices).reshape(-1).astype(np.int64, copy=False)
        if idx.size == 0:
            raise ValueError("selection is empty")
        return idx

    def _ensure_pca(
        self,
        adata: ad.AnnData,
        subset: ad.AnnData,
        sel_idx: np.ndarray,
        params: dict,
        progress: ProgressCallback,
    ) -> int:
        """Ensure ``subset.obsm['X_pca']`` exists, reusing source rows if any.

        If the source dataset already has an ``X_pca`` obsm, the corresponding
        rows are sliced into ``subset`` (cheap, no recompute). Otherwise PCA is
        computed on the subset.

        Args:
            adata: The source AnnData (used to reuse existing ``X_pca``).
            subset: The in-memory subset to populate.
            sel_idx: The selection indices used to build ``subset`` (same order).
            params: Compute params; ``n_pcs`` caps the components used.
            progress: Callback ``(step, fraction, message)``.

        Returns:
            The number of principal components available for neighbors.
        """
        if "X_pca" in subset.obsm and np.asarray(subset.obsm["X_pca"]).shape[1] >= 2:
            existing = np.asarray(subset.obsm["X_pca"])
            progress("pca", 0.3, "Reusing existing PCA")
        elif "X_pca" in adata.obsm and np.asarray(adata.obsm["X_pca"]).shape[1] >= 2:
            existing = np.ascontiguousarray(
                np.asarray(adata.obsm["X_pca"])[sel_idx, :]
            )
            subset.obsm["X_pca"] = existing
            progress("pca", 0.3, "Reusing existing PCA rows")
        else:
            progress("pca", 0.2, "Computing PCA")
            sc.pp.pca(subset)
            existing = np.asarray(subset.obsm["X_pca"])
            progress("pca", 0.35, "PCA complete")

        available = int(existing.shape[1])
        requested = params.get("n_pcs")
        if requested is None:
            return min(available, 50)
        return max(1, min(available, int(requested)))

    @staticmethod
    def _run_leiden(subset: ad.AnnData, resolution: float) -> None:
        """Run Leiden clustering, preferring the fast igraph flavor.

        Falls back to scanpy's default flavor when the igraph flavor is not
        supported by the installed scanpy/leidenalg versions.

        Args:
            subset: The in-memory AnnData with a neighbors graph computed.
            resolution: Leiden resolution parameter.
        """
        try:
            sc.tl.leiden(
                subset,
                resolution=resolution,
                flavor="igraph",
                n_iterations=2,
                directed=False,
            )
        except (TypeError, ValueError, ImportError) as exc:
            logger.warning(
                "igraph leiden flavor unavailable (%s); falling back to default",
                exc,
            )
            sc.tl.leiden(subset, resolution=resolution)

    @staticmethod
    def _looks_like_counts(matrix: object) -> bool:
        """Heuristically decide whether ``X`` holds raw integer counts.

        Considers the data "raw counts" when the maximum value is large
        (``> 50``) and the sampled values are (near-)integers, indicating the
        matrix has not been normalized/log-transformed.

        Args:
            matrix: A dense or sparse expression matrix.

        Returns:
            ``True`` if the data looks like raw counts.
        """
        if sparse.issparse(matrix):
            data = matrix.data
        else:
            data = np.asarray(matrix).reshape(-1)
        if data.size == 0:
            return False
        sample = data[: min(data.size, 100000)]
        finite = sample[np.isfinite(sample)]
        if finite.size == 0:
            return False
        if float(np.max(finite)) <= 50.0:
            return False
        # Integer-like: values close to their rounded counterparts.
        frac = np.abs(finite - np.round(finite))
        return bool(np.mean(frac < 1e-6) > 0.99)


def _finite(value: float, default: float = 0.0) -> float:
    """Return ``value`` as a finite float, substituting ``default`` for NaN/inf.

    Args:
        value: The candidate value.
        default: Replacement for non-finite values.

    Returns:
        A finite Python float.
    """
    out = float(value)
    if math.isnan(out) or math.isinf(out):
        return default
    return out


# Module-level singleton used across the application (CONTRACT section 8).
service = AnnDataService()
