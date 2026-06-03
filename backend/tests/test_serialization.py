# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the binary serialization helpers (:mod:`app.serialization`).

These tests pin the binary protocol invariants from ``docs/CONTRACT.md``
sections 4 and 9:

* Coordinates and continuous values are little-endian ``Float32``.
* Categorical codes and cell indices are little-endian ``Int32``.
* ``byteLength == 4 * n`` for both dtypes (one 4-byte element per value).
* Embeddings are *interleaved xy* (``[x0, y0, x1, y1, ...]``, deck.gl
  ``size: 2``); per-cell scalars are flat (``size: 1``).
* Header builders emit the documented ``X-Cellscope-*`` string keys / values.

The module under test is owned by another agent but is built to the same
contract; these tests therefore assert the contract verbatim.
"""

from __future__ import annotations

import numpy as np

from app import serialization as ser


# --------------------------------------------------------------------------- #
# Float32 / Int32 round-trips and byte layout
# --------------------------------------------------------------------------- #
def test_encode_float32_byte_length_and_endianness() -> None:
    """``encode_float32`` yields little-endian bytes of length ``4 * n``."""
    values = np.array([0.0, 1.5, -2.25, 3.0e9, np.float32(0.1)], dtype=np.float32)
    blob = ser.encode_float32(values)

    assert isinstance(blob, (bytes, bytearray))
    assert len(blob) == 4 * values.size

    # Decode with an explicit little-endian dtype and compare bit-for-bit.
    decoded = np.frombuffer(bytes(blob), dtype="<f4")
    np.testing.assert_array_equal(decoded, values.astype("<f4"))


def test_encode_int32_byte_length_and_endianness() -> None:
    """``encode_int32`` yields little-endian bytes of length ``4 * n``."""
    values = np.array([-1, 0, 1, 7, 2_000_000_000], dtype=np.int32)
    blob = ser.encode_int32(values)

    assert isinstance(blob, (bytes, bytearray))
    assert len(blob) == 4 * values.size

    decoded = np.frombuffer(bytes(blob), dtype="<i4")
    np.testing.assert_array_equal(decoded, values.astype("<i4"))


def test_float32_round_trip_via_numpy() -> None:
    """Encoding then decoding Float32 returns the original array exactly.

    The contract only mandates a server-side *encoder*; the wire is decoded on
    the client. We therefore round-trip the bytes back with an explicit
    little-endian ``<f4`` view (mirroring the TypeScript ``Float32Array`` reader)
    and assert bit-for-bit equality.
    """
    rng = np.random.default_rng(7)
    values = rng.normal(size=257).astype(np.float32)

    blob = ser.encode_float32(values)
    decoded = np.frombuffer(bytes(blob), dtype="<f4")

    assert decoded.dtype == np.dtype("<f4")
    assert decoded.size == values.size
    np.testing.assert_array_equal(decoded, values)


def test_int32_round_trip() -> None:
    """Encoding then decoding Int32 returns the original array exactly.

    Uses the module's own :func:`~app.serialization.decode_int32` (the inbound
    selection-index decoder) to close the loop.
    """
    rng = np.random.default_rng(11)
    values = rng.integers(-1, 50, size=129).astype(np.int32)

    blob = ser.encode_int32(values)
    decoded = ser.decode_int32(bytes(blob))

    assert decoded.dtype == np.int32
    assert decoded.size == values.size
    np.testing.assert_array_equal(decoded, values)


def test_decode_int32_is_writeable_native_copy() -> None:
    """``decode_int32`` returns a writeable, native-endian ``int32`` copy."""
    blob = ser.encode_int32(np.array([3, 1, 2], dtype=np.int32))

    decoded = ser.decode_int32(blob)

    assert decoded.dtype == np.int32
    assert decoded.flags.writeable
    # Mutating the result must not raise (it does not alias immutable bytes).
    decoded[0] = 99
    assert decoded[0] == 99


def test_float32_round_trip_preserves_nan() -> None:
    """NaN sentinels survive a Float32 round-trip (missing continuous values)."""
    values = np.array([1.0, np.nan, -3.0, np.nan], dtype=np.float32)

    decoded = np.frombuffer(bytes(ser.encode_float32(values)), dtype="<f4")

    assert np.isnan(decoded[1]) and np.isnan(decoded[3])
    np.testing.assert_array_equal(decoded[[0, 2]], values[[0, 2]])


def test_encode_casts_to_contiguous_little_endian() -> None:
    """Non-contiguous / wrong-dtype input is coerced before encoding.

    The service layer promises C-contiguous wire dtypes, but the encoders must
    also be robust to a Fortran-ordered or float64 source array.
    """
    source = np.asfortranarray(
        np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64)
    )
    flat = source.reshape(-1)  # non-contiguous view in F-order
    blob = ser.encode_float32(flat)

    assert len(blob) == 4 * flat.size
    decoded = np.frombuffer(bytes(blob), dtype="<f4")
    np.testing.assert_allclose(decoded, flat.astype(np.float32))


# --------------------------------------------------------------------------- #
# Interleaved xy embedding layout
# --------------------------------------------------------------------------- #
def test_interleaved_xy_layout() -> None:
    """A ``(n, 2)`` embedding encodes as ``[x0, y0, x1, y1, ...]`` Float32."""
    coords = np.array(
        [[10.0, 20.0], [30.0, 40.0], [-1.0, -2.0]], dtype=np.float32
    )

    blob = ser.encode_interleaved_xy(coords)

    assert len(blob) == 8 * coords.shape[0]  # 2 floats * 4 bytes per cell
    decoded = np.frombuffer(bytes(blob), dtype="<f4")
    np.testing.assert_array_equal(
        decoded, np.array([10, 20, 30, 40, -1, -2], dtype=np.float32)
    )

    # Reshaping back to (n, 2) must recover the original coordinates.
    np.testing.assert_array_equal(decoded.reshape(-1, 2), coords)


def test_interleaved_xy_matches_flat_float32_encoding() -> None:
    """Interleaved-xy of a C-contiguous ``(n, 2)`` equals a flat Float32 encode.

    A C-contiguous ``(n, 2)`` array already lays out as ``[x0, y0, x1, y1, ...]``
    when flattened, so the two encoders must agree byte-for-byte.
    """
    coords = np.ascontiguousarray(
        np.array([[1.0, -1.0], [2.5, 9.0]], dtype=np.float32)
    )

    assert ser.encode_interleaved_xy(coords) == ser.encode_float32(coords)


# --------------------------------------------------------------------------- #
# bounds_of / nan_aware_minmax
# --------------------------------------------------------------------------- #
def test_bounds_of_returns_min_x_min_y_max_x_max_y() -> None:
    """``bounds_of`` returns ``(minX, minY, maxX, maxY)`` for ``(n, 2)`` coords."""
    coords = np.array(
        [[1.0, 5.0], [-2.0, 9.0], [3.0, -4.0]], dtype=np.float32
    )

    bounds = ser.bounds_of(coords)

    assert tuple(bounds) == (-2.0, -4.0, 3.0, 9.0)


def test_bounds_of_ignores_nan() -> None:
    """``bounds_of`` ignores NaN coordinates when computing the extent."""
    coords = np.array(
        [[np.nan, 1.0], [2.0, np.nan], [-5.0, 7.0]], dtype=np.float32
    )

    min_x, min_y, max_x, max_y = ser.bounds_of(coords)

    assert min_x == -5.0
    assert max_x == 2.0
    assert min_y == 1.0
    assert max_y == 7.0


def test_bounds_of_empty_is_finite() -> None:
    """An empty embedding yields finite, zero bounds (header stays well-formed)."""
    coords = np.empty((0, 2), dtype=np.float32)

    assert ser.bounds_of(coords) == (0.0, 0.0, 0.0, 0.0)


def test_nan_aware_minmax_ignores_nan() -> None:
    """``nan_aware_minmax`` returns the min/max excluding NaN entries."""
    values = np.array([np.nan, 2.0, -1.0, np.nan, 7.5], dtype=np.float32)

    vmin, vmax = ser.nan_aware_minmax(values)

    assert vmin == -1.0
    assert vmax == 7.5


def test_nan_aware_minmax_all_nan_is_finite() -> None:
    """An all-NaN input yields finite, non-NaN bounds (so JSON stays valid)."""
    values = np.array([np.nan, np.nan], dtype=np.float32)

    vmin, vmax = ser.nan_aware_minmax(values)

    assert np.isfinite(vmin)
    assert np.isfinite(vmax)


# --------------------------------------------------------------------------- #
# format_bounds round-trip
# --------------------------------------------------------------------------- #
def test_format_bounds_is_four_parseable_floats() -> None:
    """``format_bounds`` emits four comma-separated, parseable floats."""
    text = ser.format_bounds((-2.0, -4.0, 3.0, 9.0))

    parsed = [float(token) for token in text.split(",")]
    assert parsed == [-2.0, -4.0, 3.0, 9.0]


# --------------------------------------------------------------------------- #
# Header builders — documented X-Cellscope-* string keys / values (CONTRACT 4)
# --------------------------------------------------------------------------- #
def test_embedding_headers_documented_keys_and_values() -> None:
    """Embedding headers match CONTRACT 4.5 keys and string values exactly."""
    headers = ser.embedding_headers(
        n_obs=300, key="X_umap", bounds=(-2.0, -4.0, 3.0, 9.0)
    )

    assert headers["X-Cellscope-Dtype"] == "float32"
    assert headers["X-Cellscope-Layout"] == "interleaved-xy"
    assert headers["X-Cellscope-N-Obs"] == "300"
    assert headers["X-Cellscope-Key"] == "X_umap"

    # Bounds: 4 comma-separated floats, parseable back to the input.
    parsed = [float(token) for token in headers["X-Cellscope-Bounds"].split(",")]
    assert len(parsed) == 4
    assert parsed == [-2.0, -4.0, 3.0, 9.0]

    # Header values are always strings (HTTP requires str headers).
    assert all(isinstance(value, str) for value in headers.values())


def test_expression_headers_documented_keys_and_values() -> None:
    """Expression headers match CONTRACT 4.6 keys and string values exactly."""
    headers = ser.expression_headers(
        n_obs=300, gene="CD3D", vmin=0.0, vmax=5.0
    )

    assert headers["X-Cellscope-Dtype"] == "float32"
    assert headers["X-Cellscope-N-Obs"] == "300"
    assert headers["X-Cellscope-Gene"] == "CD3D"
    assert float(headers["X-Cellscope-Min"]) == 0.0
    assert float(headers["X-Cellscope-Max"]) == 5.0
    assert all(isinstance(value, str) for value in headers.values())


def test_obs_categorical_headers_documented_keys_and_values() -> None:
    """Categorical obs headers match CONTRACT 4.6 keys / string values."""
    headers = ser.obs_categorical_headers(n_obs=300, n_categories=4)

    assert headers["X-Cellscope-Kind"] == "categorical"
    assert headers["X-Cellscope-Dtype"] == "int32"
    assert headers["X-Cellscope-N-Obs"] == "300"
    assert headers["X-Cellscope-N-Categories"] == "4"
    assert all(isinstance(value, str) for value in headers.values())


def test_obs_continuous_headers_documented_keys_and_values() -> None:
    """Continuous obs headers match CONTRACT 4.6 keys / string values."""
    headers = ser.obs_continuous_headers(n_obs=300, vmin=-1.5, vmax=42.0)

    assert headers["X-Cellscope-Kind"] == "continuous"
    assert headers["X-Cellscope-Dtype"] == "float32"
    assert headers["X-Cellscope-N-Obs"] == "300"
    assert float(headers["X-Cellscope-Min"]) == -1.5
    assert float(headers["X-Cellscope-Max"]) == 42.0
    assert all(isinstance(value, str) for value in headers.values())


def test_recluster_headers_documented_keys_and_values() -> None:
    """Recluster job-result headers match CONTRACT 4.8 keys / string values."""
    headers = ser.recluster_headers(n_selected=120, n_clusters=5)

    assert headers["X-Cellscope-Job-Type"] == "recluster"
    assert headers["X-Cellscope-N"] == "120"
    assert headers["X-Cellscope-N-Clusters"] == "5"
    assert headers["X-Cellscope-Dtype"] == "int32"
    assert all(isinstance(value, str) for value in headers.values())


def test_recompute_umap_headers_documented_keys_and_values() -> None:
    """Recompute-UMAP job-result headers match CONTRACT 4.8 keys / values."""
    headers = ser.recompute_umap_headers(
        n_selected=120, bounds=(-1.0, -2.0, 3.0, 4.0)
    )

    assert headers["X-Cellscope-Job-Type"] == "recompute_umap"
    assert headers["X-Cellscope-N"] == "120"
    assert headers["X-Cellscope-Dtype"] == "float32"
    parsed = [float(token) for token in headers["X-Cellscope-Bounds"].split(",")]
    assert parsed == [-1.0, -2.0, 3.0, 4.0]
    assert all(isinstance(value, str) for value in headers.values())
