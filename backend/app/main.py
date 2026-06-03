# SPDX-License-Identifier: GPL-3.0-or-later
"""FastAPI application factory and entrypoint.

Builds the CellScope ASGI application: wires CORS, mounts the API routers under
the ``/api`` prefix, exposes a health check, optionally auto-loads a dataset at
startup, and (when configured) serves the built frontend via ``StaticFiles``
with an SPA fallback. See ``docs/CONTRACT.md`` sections 4, 5 and the serving
model in section 1.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import cors_origins, settings
from app.routers import color, datasets, jobs, selection
from app.services.anndata_service import service

logger = logging.getLogger("cellscope")


def _configure_logging() -> None:
    """Configure root logging once if the host has not already done so.

    Uses ``logging.basicConfig`` at INFO level. This is a no-op if the root
    logger already has handlers (e.g. configured by uvicorn).
    """
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )


def _autoload() -> None:
    """Best-effort auto-load of ``CELLSCOPE_AUTOLOAD`` at startup.

    Failures are logged and swallowed so the server still starts when the
    configured dataset is missing or invalid.
    """
    autoload_path = settings.autoload.strip()
    if not autoload_path:
        return
    try:
        info = service.load(autoload_path)
        logger.info(
            "Autoloaded dataset %s from %s (%d obs x %d vars)",
            info.dataset_id,
            autoload_path,
            info.n_obs,
            info.n_vars,
        )
    except Exception:  # noqa: BLE001 - best-effort, must not block startup
        logger.exception("Autoload of %s failed", autoload_path)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: run the best-effort autoload on startup.

    Replaces the deprecated ``@app.on_event("startup")`` hook. No shutdown
    actions are required.
    """
    _autoload()
    yield


def create_app() -> FastAPI:
    """Create and configure the CellScope FastAPI application.

    Wires CORS middleware from :data:`app.config.settings`, includes the
    ``datasets``, ``color``, ``selection`` and ``jobs`` routers under the
    ``/api`` prefix, registers the ``GET /api/health`` endpoint, attaches an
    autoload startup hook, and — when ``CELLSCOPE_STATIC_DIR`` points at an
    existing directory — mounts the built frontend with an SPA fallback.

    Returns:
        The fully configured :class:`fastapi.FastAPI` instance.
    """
    _configure_logging()

    app = FastAPI(
        title="CellScope",
        version=__version__,
        description="Self-hostable single-cell RNA-seq browser backend.",
        lifespan=_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    # API routers. Routers define paths WITHOUT the /api prefix; we add it here.
    app.include_router(datasets.router, prefix="/api")
    app.include_router(color.router, prefix="/api")
    app.include_router(selection.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """Return a liveness payload with the running backend version.

        Returns:
            A mapping ``{"status": "ok", "version": <package version>}``.
        """
        return {"status": "ok", "version": __version__}

    _mount_static(app)
    return app


def _mount_static(app: FastAPI) -> None:
    """Mount the built frontend and an SPA fallback when configured.

    If :attr:`app.config.settings.static_dir` is set and exists, mount it at
    ``/`` (serving ``index.html`` for the root) and register a catch-all GET
    route returning ``index.html`` for any non-``/api`` path so client-side
    routing works on hard refresh. No-op when the directory is unset/missing.

    Args:
        app: The application to attach the static handlers to.
    """
    static_dir = settings.static_dir.strip()
    if not static_dir:
        return

    static_path = Path(static_dir)
    if not static_path.is_dir():
        logger.warning("CELLSCOPE_STATIC_DIR %s does not exist; serving API only", static_dir)
        return

    index_file = static_path / "index.html"

    # SPA fallback: any non-/api GET that did not match an API route or a static
    # asset returns index.html so the client router can handle the path.
    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str, request: Request) -> FileResponse | JSONResponse:
        """Serve a static asset if present, else fall back to ``index.html``.

        Args:
            full_path: The captured request path (relative to root).
            request: The incoming request (used to guard the ``/api`` prefix).

        Returns:
            A :class:`FileResponse` for the matched asset or ``index.html``, or
            a 404 :class:`JSONResponse` when the SPA shell is unavailable.
        """
        if full_path.startswith("api/") or full_path == "api":
            return JSONResponse(status_code=404, content={"detail": "Not Found"})

        candidate = (static_path / full_path).resolve()
        # Guard against path traversal: candidate must stay within static_path.
        try:
            candidate.relative_to(static_path.resolve())
            if candidate.is_file():
                return FileResponse(candidate)
        except ValueError:
            pass

        if index_file.is_file():
            return FileResponse(index_file)
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    # Mount StaticFiles last so hashed assets resolve directly; the SPA fallback
    # above handles unmatched client-routes.
    app.mount("/", StaticFiles(directory=str(static_path), html=True), name="static")


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=bool(os.environ.get("CELLSCOPE_RELOAD")),
    )
