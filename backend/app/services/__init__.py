# SPDX-License-Identifier: GPL-3.0-or-later
"""Service layer package for CellScope.

Exposes the :class:`~app.services.anndata_service.AnnDataService` class and the
module-level :data:`service` singleton used by the routers and job manager to
load AnnData files and run all heavy reads/compute (CONTRACT section 8).
"""

from __future__ import annotations

from app.services.anndata_service import AnnDataService, service

__all__ = ["AnnDataService", "service"]
