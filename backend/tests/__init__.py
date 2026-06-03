# SPDX-License-Identifier: GPL-3.0-or-later
"""Test package for the CellScope backend.

Contains the synthetic-AnnData fixtures (:mod:`tests.conftest`) and the pytest
suites exercising the binary serialization helpers, the AnnData service layer,
and the FastAPI REST API. No test in this package ever downloads data: every
fixture is built from a small, deterministic synthetic dataset (see
``docs/CONTRACT.md`` section 10).
"""
