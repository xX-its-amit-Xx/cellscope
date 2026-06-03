# SPDX-License-Identifier: GPL-3.0-or-later
"""Application configuration.

Defines the :class:`Settings` model (backed by ``pydantic-settings``) that reads
the ``CELLSCOPE_*`` environment variables documented in section 3 of
``docs/CONTRACT.md``. A module-level :data:`settings` singleton is provided for
import throughout the application.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration read from ``CELLSCOPE_*`` environment variables.

    All fields use the documented defaults from CONTRACT section 3. The
    ``env_prefix`` is ``CELLSCOPE_`` so e.g. the field :attr:`host` is read from
    the ``CELLSCOPE_HOST`` environment variable.

    Attributes:
        host: Bind host for the uvicorn server.
        port: Bind port for the uvicorn server.
        data_dir: Directory scanned for ``.h5ad`` files and where uploads land.
        backed_threshold_mb: Files at or above this size open in backed mode.
        max_upload_mb: Reject uploads larger than this many megabytes.
        marker_rest_cap: Maximum number of "rest" cells sampled for markers.
        selection_lru: Number of cached selections to retain (LRU eviction).
        static_dir: Path to the built frontend assets; empty means API-only.
        cors_origins: Comma-separated list of allowed CORS origins.
        autoload: Optional ``.h5ad`` path auto-loaded at startup.
    """

    model_config = SettingsConfigDict(
        env_prefix="CELLSCOPE_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: str = "./data"
    backed_threshold_mb: int = 500
    max_upload_mb: int = 5120
    marker_rest_cap: int = 50000
    selection_lru: int = 64
    static_dir: str = ""
    cors_origins: str = "*"
    autoload: str = ""

    def cors_origins_list(self) -> list[str]:
        """Split :attr:`cors_origins` into a list of trimmed origin strings.

        Returns:
            The configured origins as a list. A bare ``"*"`` (or empty value)
            yields ``["*"]`` so the middleware allows all origins.
        """
        raw = self.cors_origins.strip()
        if not raw or raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]


settings = Settings()


def cors_origins() -> list[str]:
    """Return the configured CORS origins as a list.

    Convenience helper splitting the comma-separated ``CELLSCOPE_CORS_ORIGINS``
    value held on the module-level :data:`settings` singleton.

    Returns:
        The list of allowed origins (``["*"]`` for the permissive default).
    """
    return settings.cors_origins_list()
