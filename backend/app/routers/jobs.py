# SPDX-License-Identifier: GPL-3.0-or-later
"""WebSocket job protocol and binary job-result download.

Implements ``docs/CONTRACT.md`` sections 4.8 and 5:

* ``WebSocket /api/ws/jobs`` — submit a ``recluster`` or ``recompute_umap`` job,
  stream coarse progress, and receive a ``completed`` message pointing at the
  binary result URL. Cancellation is best-effort (scanpy calls are not
  mid-flight interruptible).
* ``GET /api/jobs/{job_id}/result`` — download the stored, already-encoded
  little-endian binary result with the contract headers.

The heavy compute (:func:`app.services.service.recluster` /
:func:`~app.services.service.recompute_umap`) runs in a worker thread via
:func:`asyncio.to_thread`. A thread-safe :class:`asyncio.Queue` carries
progress messages back to the event loop; the worker pushes a sentinel when it
finishes so the drain coroutine terminates cleanly (no deadlock).

Routes are declared *without* the ``/api`` prefix — it is applied once when the
router is mounted in :func:`app.main.create_app`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Response, WebSocket, WebSocketDisconnect
from starlette.status import WS_1011_INTERNAL_ERROR

from app import serialization
from app.jobs import Job, job_manager
from app.services import service

logger = logging.getLogger(__name__)

router = APIRouter()

#: Job types accepted over the WebSocket (CONTRACT section 5).
_VALID_JOB_TYPES = ("recluster", "recompute_umap")

#: Sentinel pushed onto the progress queue by the worker thread to signal that
#: no further progress messages will arrive and the drain coroutine may stop.
_DRAIN_SENTINEL: object = object()


def _summary_recluster(n_cells: int, n_clusters: int) -> dict[str, int]:
    """Build the ``completed`` summary payload for a recluster job.

    Args:
        n_cells: Number of selected cells (length of the label array).
        n_clusters: Number of distinct clusters produced.

    Returns:
        A JSON-serializable summary dict (CONTRACT section 5).
    """
    return {"n_cells": n_cells, "n_clusters": n_clusters}


def _summary_umap(n_cells: int) -> dict[str, int]:
    """Build the ``completed`` summary payload for a recompute-UMAP job.

    Args:
        n_cells: Number of selected cells (half the interleaved-xy length).

    Returns:
        A JSON-serializable summary dict (CONTRACT section 5).
    """
    return {"n_cells": n_cells}


async def _run_job(
    ws: WebSocket,
    job: Job,
    selection_id: str | None,
    params: dict[str, Any],
    send_lock: asyncio.Lock,
) -> None:
    """Execute one submitted job end-to-end and stream messages to the client.

    Resolves the selection, runs the heavy compute in a worker thread while
    draining progress messages onto the socket, encodes and stores the binary
    result, and finally sends a ``completed`` (or ``error``) message. All
    exceptions are caught and reported to the client as an ``error`` message so
    a single bad job never tears down the socket.

    Runs as a background task spawned by :func:`_handle_submit`, so it uses
    ``send_lock`` to avoid interleaving writes with the receive loop's cancel
    acknowledgements.

    Args:
        ws: The accepted WebSocket connection.
        job: The job created for this submission (already ``accepted``).
        selection_id: The registered selection id to scope compute to.
        params: Job-type-specific parameters (``resolution``/``n_neighbors``/…).
        send_lock: Lock serializing writes to ``ws``.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue()

    def progress(step: str, frac: float, message: str) -> None:
        """Worker-thread progress callback; thread-safely enqueues a message.

        Matches the service ``Callable[[str, float, str], None]`` contract.

        Args:
            step: Coarse pipeline step label (CONTRACT section 5).
            frac: Progress fraction in [0, 1].
            message: Human-readable status message.
        """
        msg: dict[str, Any] = {
            "type": "progress",
            "job_id": job.id,
            "step": step,
            "progress": float(frac),
            "message": message,
        }
        loop.call_soon_threadsafe(queue.put_nowait, msg)

    def worker() -> tuple[np.ndarray, Any]:
        """Run the heavy compute on a worker thread.

        Returns:
            For ``recluster``: ``(int32 labels [n], n_clusters)``.
            For ``recompute_umap``: ``(float32 [2n] interleaved xy, bounds)``.
        """
        indices = service.resolve_selection(job.dataset_id, selection_id, None)
        if job.job_type == "recluster":
            return service.recluster(job.dataset_id, indices, params, progress)
        return service.recompute_umap(job.dataset_id, indices, params, progress)

    job.status = "running"
    worker_task = asyncio.create_task(asyncio.to_thread(worker))

    # Ensure the drain coroutine always terminates: when the worker finishes
    # (success or failure), push the sentinel from whichever thread completes
    # the task's callbacks.
    def _on_done(_task: asyncio.Task[Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, _DRAIN_SENTINEL)

    worker_task.add_done_callback(_on_done)

    try:
        # Drain progress messages until the worker's sentinel arrives. The
        # sentinel is pushed by the done-callback above, so this loop always
        # terminates once the worker finishes (no deadlock).
        while True:
            item = await queue.get()
            if item is _DRAIN_SENTINEL:
                break
            # ``item`` is a progress dict here (only dicts and the sentinel are
            # ever enqueued).
            await _safe_send(ws, send_lock, item)  # type: ignore[arg-type]

        # Worker is done; surface its result or exception.
        try:
            result = await worker_task
        except Exception as exc:  # noqa: BLE001 - reported to the client as error
            logger.exception("Job %s failed", job.id)
            job_manager.set_error(job.id, str(exc))
            await _safe_send(
                ws, send_lock, {"type": "error", "job_id": job.id, "error": str(exc)}
            )
            return

        if job.cancel_event.is_set():
            # Cancellation was requested mid-flight; the ``cancelled`` ack was
            # already sent by the receive loop. Suppress the result.
            job_manager.set_status(job.id, "cancelled")
            return

        if job.job_type == "recluster":
            labels, n_clusters = result
            labels = np.ascontiguousarray(labels, dtype=np.int32)
            result_bytes = serialization.encode_int32(labels)
            meta: dict[str, Any] = {
                "n": int(labels.shape[0]),
                "n_clusters": int(n_clusters),
            }
            summary = _summary_recluster(int(labels.shape[0]), int(n_clusters))
        else:
            coords, bounds = result
            coords = np.ascontiguousarray(coords, dtype=np.float32).reshape(-1)
            result_bytes = serialization.encode_float32(coords)
            n_cells = int(coords.shape[0] // 2)
            meta = {"n": n_cells, "bounds": tuple(float(b) for b in bounds)}
            summary = _summary_umap(n_cells)

        job_manager.store_result(job.id, result_bytes, meta)
        await _safe_send(
            ws,
            send_lock,
            {
                "type": "completed",
                "job_id": job.id,
                "job_type": job.job_type,
                "result_url": f"/api/jobs/{job.id}/result",
                "summary": summary,
            },
        )
    except asyncio.CancelledError:
        # The socket closed and the connection handler cancelled this task. The
        # worker thread cannot be interrupted, so just stop awaiting it and let
        # it run to completion in the background; do not re-raise as an error.
        logger.info("Job %s task cancelled (socket closed)", job.id)
        job.cancel_event.set()
        raise


@router.websocket("/ws/jobs")
async def jobs_ws(ws: WebSocket) -> None:
    """WebSocket endpoint driving the recompute job protocol (CONTRACT section 5).

    Accepts the socket and loops reading JSON text frames. Supported actions:

    * ``{"action": "submit", "job_type", "dataset_id", "selection_id",
      "params"}`` — validates the request, creates a job, replies ``accepted``,
      then runs the compute and streams ``progress`` followed by ``completed``
      (or ``error``).
    * ``{"action": "cancel", "job_id"}`` — sets the job's cancel event and
      replies ``cancelled`` (best-effort; in-flight scanpy work is not
      interruptible).

    The handler tolerates client disconnects (``WebSocketDisconnect``) and
    malformed frames without crashing the server.

    Args:
        ws: The incoming WebSocket connection.
    """
    await ws.accept()
    logger.info("WebSocket /ws/jobs connected")
    # A lock serializes concurrent ``ws.send_json`` calls: while a job runs as a
    # background task and streams progress, the receive loop must stay free to
    # process ``cancel`` frames, so two coroutines may try to write at once.
    send_lock = asyncio.Lock()
    # In-flight job tasks, so they can be cancelled and awaited on disconnect.
    running: set[asyncio.Task[None]] = set()
    try:
        while True:
            payload = await ws.receive_json()
            await _handle_message(ws, payload, send_lock, running)
    except WebSocketDisconnect:
        logger.info("WebSocket /ws/jobs disconnected")
    except Exception:  # noqa: BLE001 - never leak a stack trace to the socket
        logger.exception("Unexpected error on /ws/jobs; closing")
        try:
            await ws.close(code=WS_1011_INTERNAL_ERROR)
        except RuntimeError:
            # Socket already closing/closed.
            pass
    finally:
        # Tear down any still-running job tasks so they do not outlive the
        # socket and try to send on a closed connection.
        for task in list(running):
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)


async def _safe_send(ws: WebSocket, send_lock: asyncio.Lock, msg: dict[str, Any]) -> None:
    """Serialize and best-effort send a JSON frame to the client.

    Acquires ``send_lock`` so concurrent senders (a running job task and the
    receive loop) never interleave writes on the same socket. Send failures
    (e.g. the client has disconnected) are swallowed: a dead socket must not
    crash a background job task.

    Args:
        ws: The accepted WebSocket connection.
        send_lock: Lock serializing writes to ``ws``.
        msg: The JSON-serializable message to send.
    """
    async with send_lock:
        try:
            await ws.send_json(msg)
        except (WebSocketDisconnect, RuntimeError):
            logger.debug("Dropping frame; socket closed: %s", msg.get("type"))


async def _handle_message(
    ws: WebSocket,
    payload: Any,
    send_lock: asyncio.Lock,
    running: set[asyncio.Task[None]],
) -> None:
    """Dispatch a single client frame to the appropriate handler.

    ``submit`` frames spawn the job as a background task so the receive loop
    stays responsive to ``cancel`` frames while the job runs.

    Args:
        ws: The accepted WebSocket connection.
        payload: The decoded JSON frame (expected to be a dict).
        send_lock: Lock serializing writes to ``ws``.
        running: Set tracking in-flight job tasks.
    """
    if not isinstance(payload, dict):
        await _safe_send(
            ws,
            send_lock,
            {"type": "error", "job_id": None, "error": "Frame must be a JSON object"},
        )
        return

    action = payload.get("action")
    if action == "submit":
        await _handle_submit(ws, payload, send_lock, running)
    elif action == "cancel":
        await _handle_cancel(ws, payload, send_lock)
    else:
        await _safe_send(
            ws,
            send_lock,
            {
                "type": "error",
                "job_id": payload.get("job_id"),
                "error": f"Unknown action: {action!r}",
            },
        )


async def _handle_submit(
    ws: WebSocket,
    payload: dict[str, Any],
    send_lock: asyncio.Lock,
    running: set[asyncio.Task[None]],
) -> None:
    """Validate a ``submit`` frame and spawn the job as a background task.

    Validation failures reply with an ``error`` frame. On success a job is
    created, an ``accepted`` frame is sent, and the compute is launched as a
    tracked background task so the receive loop stays responsive to ``cancel``.

    Args:
        ws: The accepted WebSocket connection.
        payload: The decoded ``submit`` frame.
        send_lock: Lock serializing writes to ``ws``.
        running: Set tracking in-flight job tasks.
    """
    job_type = payload.get("job_type")
    dataset_id = payload.get("dataset_id")
    selection_id = payload.get("selection_id")
    params = payload.get("params") or {}

    if job_type not in _VALID_JOB_TYPES:
        await _safe_send(
            ws,
            send_lock,
            {"type": "error", "job_id": None, "error": f"Invalid job_type: {job_type!r}"},
        )
        return
    if not isinstance(dataset_id, str) or not dataset_id:
        await _safe_send(
            ws, send_lock, {"type": "error", "job_id": None, "error": "Missing dataset_id"}
        )
        return
    if not isinstance(selection_id, str) or not selection_id:
        await _safe_send(
            ws,
            send_lock,
            {"type": "error", "job_id": None, "error": "Missing selection_id"},
        )
        return
    if not isinstance(params, dict):
        await _safe_send(
            ws,
            send_lock,
            {"type": "error", "job_id": None, "error": "params must be an object"},
        )
        return

    job = job_manager.create(job_type, dataset_id)
    await _safe_send(
        ws, send_lock, {"type": "accepted", "job_id": job.id, "job_type": job_type}
    )

    # Run the job concurrently so the receive loop can still process cancels.
    task = asyncio.create_task(_run_job(ws, job, selection_id, params, send_lock))
    running.add(task)
    task.add_done_callback(running.discard)


async def _handle_cancel(
    ws: WebSocket, payload: dict[str, Any], send_lock: asyncio.Lock
) -> None:
    """Handle a ``cancel`` frame (best-effort).

    Sets the job's cancel event and acknowledges with a ``cancelled`` message.
    Because scanpy compute is not mid-flight interruptible, this only prevents
    work that has not yet started and suppresses the ``completed`` message of a
    job that has not finished.

    Args:
        ws: The accepted WebSocket connection.
        payload: The decoded ``cancel`` frame.
        send_lock: Lock serializing writes to ``ws``.
    """
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        await _safe_send(
            ws, send_lock, {"type": "error", "job_id": None, "error": "Missing job_id"}
        )
        return

    job = job_manager.get(job_id)
    if job is not None:
        job.cancel_event.set()
        if job.status in ("pending", "running"):
            job_manager.set_status(job_id, "cancelled")
    await _safe_send(ws, send_lock, {"type": "cancelled", "job_id": job_id})


@router.get("/jobs/{job_id}/result")
async def get_job_result(job_id: str) -> Response:
    """Download a completed job's binary result (CONTRACT section 4.8).

    Args:
        job_id: The job's uuid hex identifier.

    Returns:
        A binary ``application/octet-stream`` response with the contract
        headers for the job type:

        * ``recluster`` → Int32 labels of length ``n``; headers
          ``X-Cellscope-Job-Type``, ``X-Cellscope-N``,
          ``X-Cellscope-N-Clusters``, ``X-Cellscope-Dtype: int32``.
        * ``recompute_umap`` → Float32 interleaved xy of length ``2n``; headers
          ``X-Cellscope-Job-Type``, ``X-Cellscope-N``, ``X-Cellscope-Bounds``,
          ``X-Cellscope-Dtype: float32``.

    Raises:
        fastapi.HTTPException: 404 if the job is unknown or not yet completed.
    """
    job = job_manager.get(job_id)
    if job is None or job.status != "completed" or job.result_bytes is None:
        raise HTTPException(status_code=404, detail="Job not found or not completed")

    meta = job.result_meta
    n = int(meta["n"])
    if job.job_type == "recluster":
        headers = serialization.recluster_headers(n, int(meta["n_clusters"]))
    else:
        bounds = tuple(float(b) for b in meta["bounds"])
        headers = serialization.recompute_umap_headers(n, bounds)

    return Response(
        content=job.result_bytes,
        media_type=serialization.MEDIA_TYPE,
        headers=headers,
    )
