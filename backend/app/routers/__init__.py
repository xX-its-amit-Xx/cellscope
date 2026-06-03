# SPDX-License-Identifier: GPL-3.0-or-later
"""REST and WebSocket routers for the CellScope backend.

Each module in this package defines an :class:`fastapi.APIRouter` instance named
``router`` whose paths are declared *without* the ``/api`` prefix; the prefix is
added once in :func:`app.main.create_app` via ``app.include_router(..., prefix="/api")``.

Submodules:
    datasets: Dataset load/upload/list/metadata and embedding download (CONTRACT 4.1-4.5).
    color:    Gene search, expression, and obs-column color sources (CONTRACT 4.6).
    selection: Selection registration and selection statistics/markers (CONTRACT 4.7).
    jobs:     WebSocket job protocol and job-result download (CONTRACT 4.8, 5).
"""

from __future__ import annotations

from app.routers import color, datasets, jobs, selection

__all__ = ["color", "datasets", "jobs", "selection"]
