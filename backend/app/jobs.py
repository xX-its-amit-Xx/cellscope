# SPDX-License-Identifier: GPL-3.0-or-later
"""Async job manager and in-memory result store.

This module provides the bookkeeping layer behind the WebSocket job protocol
(``docs/CONTRACT.md`` sections 4.8 and 5). Heavy compute itself lives in the
service layer (:mod:`app.services`); the :class:`JobManager` here only tracks
job lifecycle state and holds the serialized binary result so it can be fetched
over HTTP via ``GET /api/jobs/{job_id}/result``.

A module-level singleton :data:`job_manager` is shared by the jobs router.

The store is intentionally tiny and bounded: only the most recent
:data:`JobManager.capacity` jobs are retained; older entries are evicted in
insertion (FIFO / oldest-first) order. Results are raw, already-encoded bytes
produced by the serialization helpers in the router, so this module has no
dependency on numpy or the wire protocol details.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

#: Default maximum number of jobs retained by a :class:`JobManager`.
DEFAULT_CAPACITY = 128

#: Valid lifecycle states for a :class:`Job`.
JobStatus = Literal["pending", "running", "completed", "error", "cancelled"]


@dataclass
class Job:
    """A single recompute job and its lifecycle state.

    Instances are created by :meth:`JobManager.create` and mutated in place as
    the worker progresses. The :attr:`cancel_event` lets the WebSocket handler
    signal a best-effort cancellation request to the (otherwise opaque) compute
    thread; note that scanpy calls are not mid-flight interruptible, so setting
    the event only prevents work that has not yet started.

    Attributes:
        id: uuid4 hex identifier for the job.
        job_type: Either ``"recluster"`` or ``"recompute_umap"``.
        dataset_id: Identifier of the dataset the job operates on.
        status: Current lifecycle state (see :data:`JobStatus`).
        result_bytes: Encoded binary result, or ``None`` until completion.
        result_meta: Header/summary metadata describing :attr:`result_bytes`
            (e.g. ``{"n": 1234, "n_clusters": 7}`` or ``{"n": 1234,
            "bounds": (minx, miny, maxx, maxy)}``).
        error: Human-readable error message when :attr:`status` is ``"error"``.
        cancel_event: Set to request best-effort cancellation of the worker.
    """

    id: str
    job_type: str
    dataset_id: str
    status: JobStatus = "pending"
    result_bytes: bytes | None = None
    result_meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)


class JobManager:
    """Thread-safe, bounded registry of recompute jobs and their results.

    The manager is a small FIFO-bounded store: at most :attr:`capacity` jobs are
    retained, and creating a job beyond the cap evicts the oldest entry. All
    mutating operations take a single internal lock, which is sufficient for the
    access pattern here (one WebSocket coroutine creating/updating a job, plus
    HTTP result reads from other request handlers).

    Attributes:
        capacity: Maximum number of jobs retained before oldest-first eviction.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        """Initialize an empty job store.

        Args:
            capacity: Maximum number of jobs to retain. Must be positive.
        """
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self._jobs: "OrderedDict[str, Job]" = OrderedDict()
        self._lock = threading.Lock()

    def create(self, job_type: str, dataset_id: str) -> Job:
        """Create and register a new pending job.

        Args:
            job_type: Either ``"recluster"`` or ``"recompute_umap"``.
            dataset_id: Identifier of the dataset the job operates on.

        Returns:
            The newly created :class:`Job` (status ``"pending"``).
        """
        job = Job(id=uuid.uuid4().hex, job_type=job_type, dataset_id=dataset_id)
        with self._lock:
            self._jobs[job.id] = job
            self._evict_locked()
        logger.info(
            "Created job %s (type=%s dataset=%s)", job.id, job_type, dataset_id
        )
        return job

    def get(self, job_id: str) -> Job | None:
        """Look up a job by id.

        Args:
            job_id: The job's uuid hex identifier.

        Returns:
            The :class:`Job`, or ``None`` if unknown (or already evicted).
        """
        with self._lock:
            return self._jobs.get(job_id)

    def store_result(
        self, job_id: str, result_bytes: bytes, meta: dict[str, Any]
    ) -> None:
        """Attach an encoded result to a job and mark it completed.

        Args:
            job_id: The job's uuid hex identifier.
            result_bytes: The already-encoded little-endian binary payload.
            meta: Header/summary metadata describing ``result_bytes`` (see
                :attr:`Job.result_meta`).

        Raises:
            KeyError: If ``job_id`` is unknown (e.g. evicted under load).
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            job.result_bytes = result_bytes
            job.result_meta = dict(meta)
            job.status = "completed"
        logger.info(
            "Stored result for job %s (%d bytes)", job_id, len(result_bytes)
        )

    def set_status(self, job_id: str, status: JobStatus) -> None:
        """Update a job's lifecycle status.

        Args:
            job_id: The job's uuid hex identifier.
            status: The new status value.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.status = status

    def set_error(self, job_id: str, error: str) -> None:
        """Mark a job as errored with a human-readable message.

        Args:
            job_id: The job's uuid hex identifier.
            error: Human-readable error description.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.status = "error"
                job.error = error

    def _evict_locked(self) -> None:
        """Evict oldest jobs until the store is within :attr:`capacity`.

        The caller must already hold :attr:`_lock`.
        """
        while len(self._jobs) > self.capacity:
            evicted_id, _ = self._jobs.popitem(last=False)
            logger.debug("Evicted oldest job %s (capacity reached)", evicted_id)


#: Module-level singleton shared by the jobs router.
job_manager = JobManager()
