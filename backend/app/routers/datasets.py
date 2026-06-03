# SPDX-License-Identifier: GPL-3.0-or-later
"""Dataset lifecycle and embedding routes (CONTRACT 4.1-4.5).

This router exposes endpoints to load a dataset by server-side path, upload a
``.h5ad`` file, list loaded/discoverable datasets, fetch dataset metadata, and
stream an embedding as a little-endian interleaved-xy Float32 binary body.

All paths are declared without the ``/api`` prefix; :func:`app.main.create_app`
mounts this router with ``prefix="/api"``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel

from app import serialization
from app.config import settings
from app.models import DatasetInfo
from app.services import service
from app.services.anndata_service import NotAnH5adError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["datasets"])


class LoadRequest(BaseModel):
    """Request body for ``POST /datasets/load`` (CONTRACT 4.1).

    This is a router-local body shape (not one of the shared DTOs in CONTRACT
    section 6); the contract only specifies the JSON payload ``{"path": ...}``.

    Attributes:
        path: An absolute path or a path relative to ``CELLSCOPE_DATA_DIR``
            pointing at the ``.h5ad`` file to load.
    """

    path: str


@router.post("/datasets/load", response_model=DatasetInfo)
def load_dataset(body: LoadRequest) -> DatasetInfo:
    """Load a dataset from a server-side ``.h5ad`` path (CONTRACT 4.1).

    Args:
        body: Request body carrying the absolute or ``DATA_DIR``-relative path.

    Returns:
        The :class:`~app.models.DatasetInfo` for the newly loaded dataset.

    Raises:
        HTTPException: 400 for a bad/missing path or unreadable AnnData, 404 if
            the file does not exist, 415 if the file is not an ``.h5ad``.
    """
    path = body.path
    if not path or not path.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="A non-empty 'path' is required."
        )
    try:
        return service.load(path)
    except NotAnH5adError as exc:
        # A non-.h5ad extension is an unsupported media type per CONTRACT 4.1.
        # Deterministic: keyed on the exception type, not its message text.
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        ) from exc
    except FileNotFoundError as exc:
        logger.info("Load failed, path not found: %s", path)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (IsADirectoryError, PermissionError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OSError as exc:
        # Corrupt/unreadable file content.
        logger.exception("Failed to read AnnData at %s", path)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/datasets/upload", response_model=DatasetInfo)
async def upload_dataset(file: UploadFile) -> DatasetInfo:
    """Upload an ``.h5ad`` file, persist it under ``DATA_DIR``, and load it (CONTRACT 4.2).

    The upload is read in bounded chunks so the in-flight payload can be rejected
    with HTTP 413 as soon as it exceeds ``settings.max_upload_mb`` rather than
    after buffering an arbitrarily large body.

    Args:
        file: The multipart ``file`` field carrying the ``.h5ad`` payload.

    Returns:
        The :class:`~app.models.DatasetInfo` for the newly loaded dataset.

    Raises:
        HTTPException: 400 if no filename is provided or the file cannot be read,
            413 if the upload exceeds ``settings.max_upload_mb``, 415 if the file
            is not an ``.h5ad``.
    """
    filename = file.filename
    if not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file has no filename."
        )
    if not filename.lower().endswith(".h5ad"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only '.h5ad' files are supported.",
        )

    max_bytes = settings.max_upload_mb * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    chunk_size = 1024 * 1024  # 1 MiB
    try:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Upload exceeds the maximum of {settings.max_upload_mb} MB.",
                )
            chunks.append(chunk)
    finally:
        await file.close()

    data = b"".join(chunks)
    try:
        return service.load_uploaded(filename, data)
    except NotAnH5adError as exc:
        # Deterministic 415: keyed on the exception type, not its message text.
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OSError as exc:
        logger.exception("Failed to persist or read uploaded file %s", filename)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/datasets")
def list_datasets() -> dict[str, object]:
    """List loaded datasets and discoverable ``.h5ad`` files (CONTRACT 4.3).

    Returns:
        A mapping with ``loaded`` (a list of :class:`~app.models.DatasetSummary`)
        and ``available_files`` (``.h5ad`` filenames found in ``DATA_DIR``).
    """
    return {
        "loaded": service.list_loaded(),
        "available_files": service.available_files(),
    }


@router.get("/datasets/{dataset_id}", response_model=DatasetInfo)
def get_dataset(dataset_id: str) -> DatasetInfo:
    """Return metadata for a loaded dataset (CONTRACT 4.4).

    Args:
        dataset_id: The uuid4-hex identifier returned at load time.

    Returns:
        The :class:`~app.models.DatasetInfo` for the dataset.

    Raises:
        HTTPException: 404 if the dataset is unknown.
    """
    try:
        return service.get_info(dataset_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown dataset_id: {dataset_id}",
        ) from exc


@router.get("/datasets/{dataset_id}/embedding")
def get_embedding(
    dataset_id: str,
    key: str | None = Query(
        default=None,
        description="obsm embedding key (e.g. X_umap); defaults to default_embedding.",
    ),
) -> Response:
    """Stream an embedding as little-endian interleaved-xy Float32 (CONTRACT 4.5).

    The body is a raw Float32 array of length ``2 * n_obs`` laid out as
    ``[x0, y0, x1, y1, ...]``. The response carries the embedding headers,
    including ``X-Cellscope-N-Obs`` and ``X-Cellscope-Bounds``.

    Args:
        dataset_id: The dataset identifier.
        key: The ``obsm`` embedding key; ``None`` selects the dataset default.

    Returns:
        A binary :class:`fastapi.Response` with ``media_type`` set to the shared
        octet-stream media type and the embedding headers populated.

    Raises:
        HTTPException: 404 if the dataset or embedding key is unknown.
    """
    try:
        coords, bounds = service.get_embedding(dataset_id, key)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown dataset or embedding key: {exc}",
        ) from exc

    resolved_key = key if key is not None else service.get_info(dataset_id).default_embedding
    n_obs = int(coords.shape[0])
    payload = serialization.encode_float32(coords)
    headers = serialization.embedding_headers(
        n_obs=n_obs, key=resolved_key or "", bounds=bounds
    )
    return Response(content=payload, media_type=serialization.MEDIA_TYPE, headers=headers)
