# SPDX-License-Identifier: GPL-3.0-or-later
"""Binary serialization for the CellScope wire protocol.

This module implements the binary encoders/decoders and HTTP header builders
described in the CellScope system contract (sections 4 and 9). It is the single
place that knows how per-cell numeric arrays are turned into raw little-endian
byte buffers and how the ``X-Cellscope-*`` response headers are formatted.

Protocol invariants (contract section 9) enforced here:

* Coordinates and per-cell scalars travel as raw little-endian typed arrays,
  never JSON.
* Float32 (``"<f4"``) is used for coordinates and continuous values; Int32
  (``"<i4"``) is used for categorical codes and cell indices. Buffers are never
  mixed-dtype.
* Embedding and recomputed-UMAP buffers are interleaved xy (``[x0, y0, x1, y1,
  ...]``, deck.gl ``size: 2``); expression, obs and label buffers are flat
  per-cell (``size: 1``).
* ``n_obs`` (or ``n`` for job results) is always echoed in a header so the
  client can assert ``byteLength / 4`` matches the element count.

This module intentionally has **no FastAPI dependency** (numpy + standard
library only) so it can be unit-tested in isolation.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

# Media type used for every binary response in the protocol (contract section 4).
MEDIA_TYPE: str = "application/octet-stream"

# Little-endian dtype tags used on the wire.
_F4 = np.dtype("<f4")
_I4 = np.dtype("<i4")


def encode_float32(arr: np.ndarray) -> bytes:
    """Encode an array as raw little-endian Float32 bytes.

    The input is coerced to ``np.float32``, made C-contiguous and converted to
    little-endian byte order before its raw buffer is returned. This is used for
    coordinates and continuous per-cell values (contract section 9, invariant 3).

    Args:
        arr: Array-like of numeric values. Any shape is accepted; the bytes are
            emitted in C (row-major) order.

    Returns:
        The raw little-endian Float32 buffer (``4 * arr.size`` bytes).
    """
    out = np.ascontiguousarray(arr, dtype=_F4)
    return out.tobytes()


def encode_int32(arr: np.ndarray) -> bytes:
    """Encode an array as raw little-endian Int32 bytes.

    The input is coerced to ``np.int32``, made C-contiguous and converted to
    little-endian byte order before its raw buffer is returned. This is used for
    categorical codes and cell indices (contract section 9, invariant 3).

    Args:
        arr: Array-like of integer values. Any shape is accepted; the bytes are
            emitted in C (row-major) order.

    Returns:
        The raw little-endian Int32 buffer (``4 * arr.size`` bytes).
    """
    out = np.ascontiguousarray(arr, dtype=_I4)
    return out.tobytes()


def encode_interleaved_xy(coords_n2: np.ndarray) -> bytes:
    """Encode 2-D coordinates as an interleaved little-endian Float32 buffer.

    Produces the flat ``[x0, y0, x1, y1, ...]`` layout consumed by deck.gl with
    ``getPosition`` ``size: 2`` (contract section 9, invariant 4). Only the first
    two columns matter; the input must have shape ``(n, 2)``.

    Args:
        coords_n2: Array of shape ``(n, 2)`` holding the x/y coordinate per cell.

    Returns:
        The raw little-endian Float32 buffer of length ``2 * n`` elements
        (``8 * n`` bytes).

    Raises:
        ValueError: If ``coords_n2`` is not 2-dimensional with exactly 2 columns.
    """
    out = np.ascontiguousarray(coords_n2, dtype=_F4)
    if out.ndim != 2 or out.shape[1] != 2:
        raise ValueError(
            f"encode_interleaved_xy expects shape (n, 2), got {out.shape!r}"
        )
    # C-contiguous (n, 2) flattens to interleaved [x0, y0, x1, y1, ...].
    return out.reshape(-1).tobytes()


def decode_int32(buf: bytes) -> np.ndarray:
    """Decode a raw little-endian Int32 buffer into a numpy array.

    Used to parse uploaded selection index arrays (contract section 4.7). The
    buffer length must be a whole number of 4-byte Int32 elements.

    Args:
        buf: Raw bytes containing a little-endian Int32 array.

    Returns:
        A 1-D ``np.int32`` array with ``len(buf) // 4`` elements. The result owns
        its data (it is a copy), so the caller may freely mutate it.

    Raises:
        ValueError: If ``len(buf)`` is not a multiple of 4.
    """
    if len(buf) % 4 != 0:
        raise ValueError(
            f"int32 buffer length must be a multiple of 4, got {len(buf)} bytes"
        )
    arr = np.frombuffer(buf, dtype=_I4)
    # frombuffer returns a read-only view onto immutable bytes; copy and force a
    # native-endian, writeable int32 array for downstream consumers.
    return np.array(arr, dtype=np.int32)


def bounds_of(coords_n2: np.ndarray) -> tuple[float, float, float, float]:
    """Compute the NaN-aware axis-aligned bounds of 2-D coordinates.

    Args:
        coords_n2: Array of shape ``(n, 2)`` holding x/y coordinates. May contain
            NaN values, which are ignored.

    Returns:
        The tuple ``(minX, minY, maxX, maxY)``. If the array is empty or every
        value is NaN, ``(0.0, 0.0, 0.0, 0.0)`` is returned.

    Raises:
        ValueError: If ``coords_n2`` is not 2-dimensional with exactly 2 columns.
    """
    arr = np.asarray(coords_n2, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError(f"bounds_of expects shape (n, 2), got {arr.shape!r}")
    if arr.shape[0] == 0:
        return (0.0, 0.0, 0.0, 0.0)

    x = arr[:, 0]
    y = arr[:, 1]
    # Guard against all-NaN columns: nanmin/nanmax would emit a RuntimeWarning
    # and return NaN, which we coerce to 0.0 to keep the header well-formed.
    finite_x = np.isfinite(x).any()
    finite_y = np.isfinite(y).any()

    min_x = float(np.nanmin(x)) if finite_x else 0.0
    max_x = float(np.nanmax(x)) if finite_x else 0.0
    min_y = float(np.nanmin(y)) if finite_y else 0.0
    max_y = float(np.nanmax(y)) if finite_y else 0.0
    return (min_x, min_y, max_x, max_y)


def nan_aware_minmax(arr: np.ndarray) -> tuple[float, float]:
    """Compute the NaN-ignoring (min, max) of an array.

    Used to populate ``X-Cellscope-Min`` / ``X-Cellscope-Max`` for expression and
    continuous obs responses (contract sections 4.6).

    Args:
        arr: Array-like of numeric values. May contain NaN, which is ignored.

    Returns:
        The tuple ``(vmin, vmax)`` as Python floats. If the array is empty or all
        values are NaN, ``(0.0, 0.0)`` is returned.
    """
    flat = np.asarray(arr, dtype=np.float64).reshape(-1)
    if flat.size == 0 or not np.isfinite(flat).any():
        return (0.0, 0.0)
    return (float(np.nanmin(flat)), float(np.nanmax(flat)))


def format_bounds(bounds: tuple[float, float, float, float]) -> str:
    """Format an ``(minX, minY, maxX, maxY)`` tuple as a header string.

    The four floats are joined with commas using ``repr`` so the full Float32
    precision survives the round-trip through the header
    (``X-Cellscope-Bounds``, contract sections 4.5 and 4.8).

    Args:
        bounds: The ``(minX, minY, maxX, maxY)`` tuple.

    Returns:
        A comma-joined string, e.g. ``"-1.5,0.0,3.25,4.0"``.

    Raises:
        ValueError: If ``bounds`` does not contain exactly four values.
    """
    values = tuple(bounds)
    if len(values) != 4:
        raise ValueError(f"bounds must have 4 values, got {len(values)}")
    return ",".join(repr(float(v)) for v in values)


def embedding_headers(
    n_obs: int,
    key: str,
    bounds: tuple[float, float, float, float],
) -> dict[str, str]:
    """Build the response headers for an embedding buffer (contract 4.5).

    Args:
        n_obs: Number of cells (rows) encoded in the buffer.
        key: The obsm embedding key that was served, e.g. ``"X_umap"``.
        bounds: The ``(minX, minY, maxX, maxY)`` data-space bounds.

    Returns:
        A mapping of ``X-Cellscope-*`` header names to string values describing
        an interleaved-xy Float32 embedding buffer.
    """
    return {
        "X-Cellscope-Dtype": "float32",
        "X-Cellscope-Layout": "interleaved-xy",
        "X-Cellscope-N-Obs": str(int(n_obs)),
        "X-Cellscope-Key": str(key),
        "X-Cellscope-Bounds": format_bounds(bounds),
    }


def expression_headers(
    n_obs: int,
    gene: str,
    vmin: float,
    vmax: float,
) -> dict[str, str]:
    """Build the response headers for a gene-expression buffer (contract 4.6).

    Args:
        n_obs: Number of cells (rows) encoded in the buffer.
        gene: The resolved ``var_name`` of the gene that was served.
        vmin: Minimum expression value (NaN-aware).
        vmax: Maximum expression value (NaN-aware).

    Returns:
        A mapping of ``X-Cellscope-*`` header names to string values describing a
        flat Float32 expression buffer.
    """
    return {
        "X-Cellscope-Dtype": "float32",
        "X-Cellscope-N-Obs": str(int(n_obs)),
        "X-Cellscope-Gene": str(gene),
        "X-Cellscope-Min": repr(float(vmin)),
        "X-Cellscope-Max": repr(float(vmax)),
    }


def obs_categorical_headers(n_obs: int, n_categories: int) -> dict[str, str]:
    """Build the response headers for a categorical obs buffer (contract 4.6).

    The body is a flat Int32 array of category codes (``-1`` denotes NaN/missing);
    the category labels themselves are delivered separately via
    ``DatasetInfo.obs_columns[*].categories``.

    Args:
        n_obs: Number of cells (rows) encoded in the buffer.
        n_categories: Number of distinct categories in the column.

    Returns:
        A mapping of ``X-Cellscope-*`` header names to string values describing a
        flat Int32 categorical-codes buffer.
    """
    return {
        "X-Cellscope-Kind": "categorical",
        "X-Cellscope-Dtype": "int32",
        "X-Cellscope-N-Obs": str(int(n_obs)),
        "X-Cellscope-N-Categories": str(int(n_categories)),
    }


def obs_continuous_headers(
    n_obs: int,
    vmin: float,
    vmax: float,
) -> dict[str, str]:
    """Build the response headers for a continuous obs buffer (contract 4.6).

    Args:
        n_obs: Number of cells (rows) encoded in the buffer.
        vmin: Minimum value (NaN-aware).
        vmax: Maximum value (NaN-aware).

    Returns:
        A mapping of ``X-Cellscope-*`` header names to string values describing a
        flat Float32 continuous-value buffer.
    """
    return {
        "X-Cellscope-Kind": "continuous",
        "X-Cellscope-Dtype": "float32",
        "X-Cellscope-N-Obs": str(int(n_obs)),
        "X-Cellscope-Min": repr(float(vmin)),
        "X-Cellscope-Max": repr(float(vmax)),
    }


def recluster_headers(n_selected: int, n_clusters: int) -> dict[str, str]:
    """Build the headers for a ``recluster`` job result (contract 4.8).

    The body is a flat Int32 array of one cluster label per selected cell, in
    selection order.

    Args:
        n_selected: Number of selected cells (length of the label array).
        n_clusters: Number of distinct clusters produced.

    Returns:
        A mapping of ``X-Cellscope-*`` header names to string values describing a
        flat Int32 cluster-label buffer.
    """
    return {
        "X-Cellscope-Job-Type": "recluster",
        "X-Cellscope-N": str(int(n_selected)),
        "X-Cellscope-N-Clusters": str(int(n_clusters)),
        "X-Cellscope-Dtype": "int32",
    }


def recompute_umap_headers(
    n_selected: int,
    bounds: tuple[float, float, float, float],
) -> dict[str, str]:
    """Build the headers for a ``recompute_umap`` job result (contract 4.8).

    The body is an interleaved-xy Float32 array (length ``2 * n_selected``) giving
    the new coordinates for each selected cell, in selection order.

    Args:
        n_selected: Number of selected cells (the buffer holds ``2 * n_selected``
            Float32 elements).
        bounds: The ``(minX, minY, maxX, maxY)`` data-space bounds of the new
            coordinates.

    Returns:
        A mapping of ``X-Cellscope-*`` header names to string values describing an
        interleaved-xy Float32 coordinate buffer.
    """
    return {
        "X-Cellscope-Job-Type": "recompute_umap",
        "X-Cellscope-N": str(int(n_selected)),
        "X-Cellscope-Bounds": format_bounds(bounds),
        "X-Cellscope-Dtype": "float32",
    }
