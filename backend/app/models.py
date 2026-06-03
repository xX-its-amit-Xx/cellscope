# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared pydantic v2 DTO schemas.

These models mirror the data transfer objects in section 6 of
``docs/CONTRACT.md`` (and the WebSocket protocol of section 5) exactly. Field
names and types here are authoritative for the JSON wire format and must stay in
sync with the TypeScript mirror in ``frontend/src/types.ts``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 6.2 ObsColumnInfo
# ---------------------------------------------------------------------------


class ObsColumnInfo(BaseModel):
    """Metadata describing a single ``adata.obs`` column.

    Attributes:
        name: The obs column name.
        kind: Whether the column is ``"categorical"`` or ``"continuous"``.
        n_categories: Number of categories (categorical columns only).
        categories: Ordered category labels matching the integer codes
            (categorical columns only).
        min: Minimum value (continuous columns only).
        max: Maximum value (continuous columns only).
    """

    name: str
    kind: Literal["categorical", "continuous"]
    n_categories: int | None = None
    categories: list[str] | None = None
    min: float | None = None
    max: float | None = None


# ---------------------------------------------------------------------------
# 6.1 DatasetInfo
# ---------------------------------------------------------------------------


class DatasetInfo(BaseModel):
    """Full metadata for a loaded dataset.

    Attributes:
        dataset_id: The uuid4-hex identifier assigned at load time.
        path: The server-side source path, or ``None`` if unknown.
        n_obs: Number of observations (cells).
        n_vars: Number of variables (genes).
        backed: Whether the dataset is opened in backed (on-disk) mode.
        embeddings: Available ``obsm`` keys starting with ``X_``.
        default_embedding: Preferred embedding key, or ``None`` if none exist.
        obs_columns: Per-column metadata for ``adata.obs``.
        var_index_name: The name of ``adata.var.index`` if set.
        n_genes: Alias for :attr:`n_vars`.
    """

    dataset_id: str
    path: str | None
    n_obs: int
    n_vars: int
    backed: bool
    embeddings: list[str]
    default_embedding: str | None
    obs_columns: list[ObsColumnInfo]
    var_index_name: str | None
    n_genes: int


# ---------------------------------------------------------------------------
# 6.3 GeneHit
# ---------------------------------------------------------------------------


class GeneHit(BaseModel):
    """A single gene search result.

    Attributes:
        name: The variable (gene) name.
        index: The variable's positional index in ``adata.var``.
    """

    name: str
    index: int


# ---------------------------------------------------------------------------
# DatasetSummary (used by GET /api/datasets listing)
# ---------------------------------------------------------------------------


class DatasetSummary(BaseModel):
    """Compact summary of a loaded dataset for listing.

    Attributes:
        dataset_id: The uuid4-hex identifier of the loaded dataset.
        path: The server-side source path, or ``None`` if unknown.
        n_obs: Number of observations (cells).
        n_vars: Number of variables (genes).
    """

    dataset_id: str
    path: str | None
    n_obs: int
    n_vars: int


# ---------------------------------------------------------------------------
# 4.7 SelectionRef (selection register response)
# ---------------------------------------------------------------------------


class SelectionRef(BaseModel):
    """Reference to a server-registered selection.

    Attributes:
        selection_id: The identifier for the registered selection.
        n_cells: Number of cells in the selection.
    """

    selection_id: str
    n_cells: int


# ---------------------------------------------------------------------------
# 6.4 SelectionStatsRequest
# ---------------------------------------------------------------------------


class SelectionStatsRequest(BaseModel):
    """Request body for selection statistics / marker computation.

    Exactly one of :attr:`selection_id` or :attr:`indices` should be provided;
    :attr:`indices` is used only if :attr:`selection_id` is absent.

    Attributes:
        selection_id: Identifier of a previously registered selection.
        indices: Explicit cell indices, used if ``selection_id`` is absent.
        n_markers: Number of marker genes to return.
        obs_keys: Which obs columns to summarize; ``None`` uses the defaults.
    """

    selection_id: str | None = None
    indices: list[int] | None = None
    n_markers: int = 25
    obs_keys: list[str] | None = None


# ---------------------------------------------------------------------------
# 6.5 MarkerGene + obs summaries + SelectionStatsResponse
# ---------------------------------------------------------------------------


class MarkerGene(BaseModel):
    """A single differentially-expressed marker gene for a selection.

    Attributes:
        name: The gene (variable) name.
        score: The ranking test statistic.
        log2fc: Log2 fold change of the selection versus the rest.
        pval: Raw p-value.
        pval_adj: Adjusted p-value.
        pct_in: Fraction of selection cells expressing the gene (>0).
        pct_out: Fraction of rest cells expressing the gene (>0).
    """

    name: str
    score: float
    log2fc: float
    pval: float
    pval_adj: float
    pct_in: float
    pct_out: float


class CategoricalSummary(BaseModel):
    """Summary of a categorical obs column over a selection.

    Attributes:
        kind: Discriminator, always ``"categorical"``.
        counts: Mapping of category label to count within the selection.
        top: The most frequent category label.
    """

    kind: Literal["categorical"] = "categorical"
    counts: dict[str, int]
    top: str


class ContinuousSummary(BaseModel):
    """Summary of a continuous obs column over a selection.

    Attributes:
        kind: Discriminator, always ``"continuous"``.
        mean: Arithmetic mean over the selection.
        median: Median over the selection.
        min: Minimum value over the selection.
        max: Maximum value over the selection.
        std: Standard deviation over the selection.
    """

    kind: Literal["continuous"] = "continuous"
    mean: float
    median: float
    min: float
    max: float
    std: float


class SelectionStatsResponse(BaseModel):
    """Response body for selection statistics / markers.

    Attributes:
        n_cells: Number of cells in the selection.
        fraction: ``n_cells / n_obs``.
        n_markers: Number of marker genes returned.
        rest_cells_used: Number of "rest" cells used after capping.
        markers: The ranked marker genes.
        obs_summary: Per-column summaries keyed by obs column name.
        notes: Honest caveats about the computation (e.g. subsampling).
    """

    n_cells: int
    fraction: float
    n_markers: int
    rest_cells_used: int
    markers: list[MarkerGene]
    obs_summary: dict[str, CategoricalSummary | ContinuousSummary]
    notes: list[str]


# ---------------------------------------------------------------------------
# 5 / 6.6 WebSocket job protocol models
#
# These models are the CANONICAL REFERENCE SCHEMA for the CONTRACT section 5
# WebSocket protocol (and the section 6.6 DTOs). They define the exact frame
# shapes exchanged over ``/api/ws/jobs`` and are mirrored field-for-field by
# ``frontend/src/types.ts``.
#
# The jobs router does not bind these classes directly: it parses inbound frames
# and emits outbound frames as equivalent plain dicts (raw JSON over the socket),
# so the models are not imported there. They are kept here deliberately as the
# single source of truth for the protocol — documenting the intended shapes so
# any drift between the router's dicts and the contract is visible in review.
# Do not delete them as "dead code"; update them in lock-step with section 5.
# ---------------------------------------------------------------------------


class JobSubmit(BaseModel):
    """Client → server request to submit a long-running compute job.

    Attributes:
        action: Always ``"submit"`` for this message.
        job_type: Either ``"recluster"`` or ``"recompute_umap"``.
        dataset_id: The dataset to operate on.
        selection_id: The registered selection to operate on (required).
        params: Job-specific parameters (e.g. ``{"resolution": 1.0}``).
    """

    action: Literal["submit"] = "submit"
    job_type: Literal["recluster", "recompute_umap"]
    dataset_id: str
    selection_id: str
    params: dict = Field(default_factory=dict)


class JobCancel(BaseModel):
    """Client → server request to cancel a running job.

    Attributes:
        action: Always ``"cancel"`` for this message.
        job_id: Identifier of the job to cancel.
    """

    action: Literal["cancel"] = "cancel"
    job_id: str


class JobAccepted(BaseModel):
    """Server → client acknowledgement that a job was accepted.

    Attributes:
        type: Always ``"accepted"``.
        job_id: Identifier assigned to the accepted job.
        job_type: The job type being run.
    """

    type: Literal["accepted"] = "accepted"
    job_id: str
    job_type: Literal["recluster", "recompute_umap"]


class JobProgress(BaseModel):
    """Server → client progress update for a running job.

    Attributes:
        type: Always ``"progress"``.
        job_id: Identifier of the job.
        step: Coarse step label (e.g. ``"neighbors"``); may be unknown to UI.
        progress: Fractional progress in ``[0, 1]``.
        message: Human-readable status message.
    """

    type: Literal["progress"] = "progress"
    job_id: str
    step: str
    progress: float
    message: str


class JobCompleted(BaseModel):
    """Server → client notification that a job finished successfully.

    Attributes:
        type: Always ``"completed"``.
        job_id: Identifier of the job.
        job_type: The job type that completed.
        result_url: URL for downloading the binary result.
        summary: Job-type-specific summary (e.g. ``{"n_clusters": 7}``).
    """

    type: Literal["completed"] = "completed"
    job_id: str
    job_type: Literal["recluster", "recompute_umap"]
    result_url: str
    summary: dict


class JobError(BaseModel):
    """Server → client notification that a job failed.

    Attributes:
        type: Always ``"error"``.
        job_id: Identifier of the job.
        error: Human-readable error message.
    """

    type: Literal["error"] = "error"
    job_id: str
    error: str


class JobCancelled(BaseModel):
    """Server → client notification that a job was cancelled.

    Attributes:
        type: Always ``"cancelled"``.
        job_id: Identifier of the cancelled job.
    """

    type: Literal["cancelled"] = "cancelled"
    job_id: str
