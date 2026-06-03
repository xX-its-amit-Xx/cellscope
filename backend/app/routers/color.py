# SPDX-License-Identifier: GPL-3.0-or-later
"""Color-source routes: gene search, expression, and obs columns (CONTRACT 4.6).

Gene search returns JSON; expression and obs-column values are streamed as raw
little-endian typed arrays (Float32 for continuous, Int32 for categorical codes)
per the binary protocol invariants in CONTRACT 9.

All paths are declared without the ``/api`` prefix; :func:`app.main.create_app`
mounts this router with ``prefix="/api"``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Response, status

from app import serialization
from app.services import service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["color"])


@router.get("/datasets/{dataset_id}/genes")
def search_genes(
    dataset_id: str,
    query: str = Query(default="", description="Case-insensitive substring/prefix match."),
    limit: int = Query(default=50, ge=1, le=10000, description="Max hits to return."),
) -> dict[str, object]:
    """Search gene/var names (CONTRACT 4.6, gene search).

    An empty ``query`` returns the first ``limit`` genes.

    Args:
        dataset_id: The dataset identifier.
        query: Case-insensitive substring/prefix to match against ``var_names``.
        limit: Maximum number of hits to return.

    Returns:
        A mapping with ``hits`` (a list of :class:`~app.models.GeneHit`) and
        ``total`` (the total number of matches before truncation to ``limit``).

    Raises:
        HTTPException: 404 if the dataset is unknown.
    """
    try:
        hits, total = service.search_genes(dataset_id, query, limit)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown dataset_id: {dataset_id}",
        ) from exc
    return {"hits": hits, "total": total}


@router.get("/datasets/{dataset_id}/expression")
def get_expression(
    dataset_id: str,
    gene: str = Query(..., description="var_name or integer var index (as string)."),
    layer: str = Query(default="X", description="'X' for adata.X, else adata.layers[layer]."),
) -> Response:
    """Stream per-cell expression for one gene as Float32 (CONTRACT 4.6, expression).

    The body is a raw Float32 array of length ``n_obs``.

    Args:
        dataset_id: The dataset identifier.
        gene: A ``var_name`` or an integer var index encoded as a string.
        layer: ``"X"`` selects ``adata.X``; any other value selects
            ``adata.layers[layer]``.

    Returns:
        A binary :class:`fastapi.Response` with the expression headers populated,
        including the resolved gene name and the value min/max.

    Raises:
        HTTPException: 404 if the dataset or gene is unknown; 400 for a bad
            request parameter such as an unknown ``layer`` (CONTRACT 4.6, which
            makes ``layer`` a request parameter).
    """
    layer_arg = None if layer == "X" else layer
    try:
        values, resolved_name, vmin, vmax = service.get_expression(dataset_id, gene, layer_arg)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown dataset or gene: {exc}",
        ) from exc
    except ValueError as exc:
        # UnknownLayerError (a ValueError subclass) lands here -> 400, distinct
        # from an unknown gene/dataset which is a KeyError -> 404.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    n_obs = int(values.shape[0])
    payload = serialization.encode_float32(values)
    headers = serialization.expression_headers(
        n_obs=n_obs, gene=resolved_name, vmin=float(vmin), vmax=float(vmax)
    )
    return Response(content=payload, media_type=serialization.MEDIA_TYPE, headers=headers)


@router.get("/datasets/{dataset_id}/obs")
def get_obs(
    dataset_id: str,
    column: str = Query(..., description="obs column name to fetch."),
) -> Response:
    """Stream an obs column as Int32 codes or Float32 values (CONTRACT 4.6, obs).

    The response shape is distinguished by ``X-Cellscope-Kind``:

    * ``categorical`` -> Int32 codes of length ``n_obs`` (code ``-1`` = missing),
      with ``X-Cellscope-N-Categories``. Category labels are carried by
      ``DatasetInfo.obs_columns[*].categories`` (sent at load).
    * ``continuous`` -> Float32 values of length ``n_obs`` (``NaN`` allowed),
      with ``X-Cellscope-Min`` and ``X-Cellscope-Max``.

    Args:
        dataset_id: The dataset identifier.
        column: The ``obs`` column name.

    Returns:
        A binary :class:`fastapi.Response` carrying the appropriate obs headers.

    Raises:
        HTTPException: 404 if the dataset or column is unknown, 400 if the column
            cannot be interpreted as categorical or continuous.
    """
    try:
        result = service.get_obs(dataset_id, column)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown dataset or obs column: {exc}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if result.kind == "categorical":
        codes = result.codes
        if codes is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Categorical obs column '{column}' has no codes.",
            )
        n_obs = int(codes.shape[0])
        n_categories = len(result.categories) if result.categories is not None else 0
        payload = serialization.encode_int32(codes)
        headers = serialization.obs_categorical_headers(n_obs=n_obs, n_categories=n_categories)
        return Response(content=payload, media_type=serialization.MEDIA_TYPE, headers=headers)

    if result.kind == "continuous":
        values = result.values
        if values is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Continuous obs column '{column}' has no values.",
            )
        n_obs = int(values.shape[0])
        payload = serialization.encode_float32(values)
        headers = serialization.obs_continuous_headers(
            n_obs=n_obs, vmin=float(result.vmin), vmax=float(result.vmax)
        )
        return Response(content=payload, media_type=serialization.MEDIA_TYPE, headers=headers)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported obs column kind: {result.kind!r}",
    )
