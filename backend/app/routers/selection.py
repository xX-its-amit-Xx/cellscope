# SPDX-License-Identifier: GPL-3.0-or-later
"""Selection registration and statistics routes (CONTRACT 4.7).

Selection registration accepts either a raw little-endian Int32 body
(``application/octet-stream``, preferred — scales to millions of indices) or a
JSON body ``{"indices": [...]}``. The statistics endpoint resolves a selection
(by id or inline indices) and computes marker genes plus obs summaries.

All paths are declared without the ``/api`` prefix; :func:`app.main.create_app`
mounts this router with ``prefix="/api"``.
"""

from __future__ import annotations

import logging

import numpy as np
from fastapi import APIRouter, HTTPException, Request, status

from app import serialization
from app.models import SelectionRef, SelectionStatsRequest, SelectionStatsResponse
from app.services import service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["selection"])


@router.post("/datasets/{dataset_id}/selection", response_model=SelectionRef)
async def register_selection(dataset_id: str, request: Request) -> SelectionRef:
    """Register a cell-index selection and return a reference (CONTRACT 4.7, register).

    The request body is interpreted by ``Content-Type``:

    * ``application/octet-stream`` -> a raw little-endian Int32 array of cell
      indices (preferred).
    * ``application/json`` -> ``{"indices": [int, ...]}``.

    Args:
        dataset_id: The dataset identifier.
        request: The raw request, inspected to branch on ``Content-Type``.

    Returns:
        A :class:`~app.models.SelectionRef` with the new ``selection_id`` and
        ``n_cells``.

    Raises:
        HTTPException: 404 if the dataset is unknown, 400 for a malformed body,
            an unsupported content type, or out-of-range/invalid indices.
    """
    content_type = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()

    if content_type == "application/octet-stream":
        raw = await request.body()
        try:
            indices = serialization.decode_int32(raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Malformed Int32 selection body: {exc}",
            ) from exc
    elif content_type == "application/json":
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Body is not valid JSON."
            ) from exc
        if not isinstance(payload, dict) or "indices" not in payload:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="JSON selection body must be an object with an 'indices' array.",
            )
        raw_indices = payload["indices"]
        if not isinstance(raw_indices, list):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="'indices' must be an array."
            )
        try:
            indices = np.asarray(raw_indices, dtype=np.int32)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"'indices' must be an array of integers: {exc}",
            ) from exc
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Content-Type must be 'application/octet-stream' (raw Int32) or "
                "'application/json' ({'indices': [...]})."
            ),
        )

    try:
        selection_id, n_cells = service.register_selection(dataset_id, indices)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown dataset_id: {dataset_id}",
        ) from exc
    except ValueError as exc:
        # Out-of-range or otherwise invalid indices.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return SelectionRef(selection_id=selection_id, n_cells=n_cells)


@router.post("/datasets/{dataset_id}/selection/stats", response_model=SelectionStatsResponse)
def selection_stats(dataset_id: str, body: SelectionStatsRequest) -> SelectionStatsResponse:
    """Compute marker genes and obs summaries for a selection (CONTRACT 4.7, stats).

    The selection is resolved from ``selection_id`` when present, otherwise from
    the inline ``indices``. Markers are computed with scanpy
    ``rank_genes_groups`` (Wilcoxon) against a capped random sample of the rest.

    Args:
        dataset_id: The dataset identifier.
        body: The request specifying the selection and marker/summary options.

    Returns:
        A :class:`~app.models.SelectionStatsResponse` with markers, obs summaries,
        and honest notes (e.g. rest subsampling).

    Raises:
        HTTPException: 404 if the dataset or selection id is unknown, 400 if
            neither a selection id nor indices are provided or they are invalid.
    """
    if body.selection_id is None and body.indices is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Exactly one of 'selection_id' or 'indices' must be provided.",
        )

    try:
        indices = service.resolve_selection(dataset_id, body.selection_id, body.indices)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown dataset or selection id: {exc}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        return service.selection_stats(dataset_id, indices, body.n_markers, body.obs_keys)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown dataset_id: {dataset_id}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
