# SPDX-License-Identifier: GPL-3.0-or-later
"""CellScope backend application package.

A self-hostable single-cell RNA-seq browser backend built on FastAPI. This
package exposes the application factory (:func:`app.main.create_app`), the
configuration layer (:mod:`app.config`), shared DTO schemas (:mod:`app.models`),
and the service/router layers that implement the REST and WebSocket API
described in ``docs/CONTRACT.md``.
"""

__version__ = "0.1.0"
