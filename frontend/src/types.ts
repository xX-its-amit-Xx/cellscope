// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * Shared TypeScript types for CellScope.
 *
 * These mirror the authoritative contract:
 * - DTOs from CONTRACT section 6 (field names must match the pydantic models
 *   exactly, since they cross the JSON boundary verbatim).
 * - WebSocket job protocol from CONTRACT section 5.
 * - A handful of binary-protocol / store helper types from sections 4 and 7.
 *
 * Do not introduce field names or message variants that contradict CONTRACT.md.
 */

/* -------------------------------------------------------------------------- */
/* Section 6 — Shared DTOs                                                     */
/* -------------------------------------------------------------------------- */

/**
 * Kind discriminator for an observation column.
 *
 * Categorical columns are transferred as Int32 codes; continuous columns as
 * Float32 values (CONTRACT section 4.6).
 */
export type ObsColumnKind = "categorical" | "continuous";

/**
 * Per-column metadata describing one `adata.obs` column (CONTRACT 6.2).
 *
 * For categorical columns, `categories` is ordered to match the integer codes
 * sent on the `/obs` endpoint (code `-1` denotes NaN/missing). For continuous
 * columns, `min`/`max` give the value range.
 */
export interface ObsColumnInfo {
  /** Column name as it appears in `adata.obs`. */
  name: string;
  /** Whether the column is categorical or continuous. */
  kind: ObsColumnKind;
  /** Number of categories (categorical only). */
  n_categories: number | null;
  /** Ordered category labels indexed by the wire codes (categorical only). */
  categories: string[] | null;
  /** Minimum value (continuous only). */
  min: number | null;
  /** Maximum value (continuous only). */
  max: number | null;
}

/**
 * Full metadata for a loaded dataset (CONTRACT 6.1).
 *
 * Returned by the load/upload/metadata endpoints.
 */
export interface DatasetInfo {
  /** uuid4 hex identifying the loaded dataset. */
  dataset_id: string;
  /** Source path, or null for in-memory/derived datasets. */
  path: string | null;
  /** Number of observations (cells). */
  n_obs: number;
  /** Number of variables (genes). */
  n_vars: number;
  /** Whether the dataset is opened in backed (on-disk `X`) mode. */
  backed: boolean;
  /** Available embedding keys (obsm `X_*` keys). */
  embeddings: string[];
  /** Preferred embedding key, or null if none available. */
  default_embedding: string | null;
  /** Per-column obs metadata. */
  obs_columns: ObsColumnInfo[];
  /** Name of `adata.var.index`, or null. */
  var_index_name: string | null;
  /** Number of genes (== n_vars). */
  n_genes: number;
}

/**
 * Lightweight dataset descriptor for the "loaded datasets" listing
 * (CONTRACT section 4.3). The contract does not fully enumerate its fields, so
 * this is a structural subset of {@link DatasetInfo} sufficient for the UI.
 */
export interface DatasetSummary {
  /** uuid4 hex identifying the loaded dataset. */
  dataset_id: string;
  /** Source path, or null. */
  path: string | null;
  /** Number of observations (cells). */
  n_obs: number;
  /** Number of variables (genes). */
  n_vars: number;
  /** Whether the dataset is opened in backed mode. */
  backed: boolean;
  /** Preferred embedding key, or null. */
  default_embedding: string | null;
}

/**
 * A single gene-search hit (CONTRACT 6.3).
 */
export interface GeneHit {
  /** Variable (gene) name. */
  name: string;
  /** Position of the gene within `adata.var_names`. */
  index: number;
}

/**
 * Response of the gene-search endpoint (CONTRACT section 4.6).
 */
export interface GeneSearchResponse {
  /** Matching genes. */
  hits: GeneHit[];
  /** Total number of matches (may exceed `hits.length` due to `limit`). */
  total: number;
}

/**
 * Server-side reference to a registered selection (CONTRACT section 4.7).
 */
export interface SelectionRef {
  /** Identifier used to refer to the selection in later requests. */
  selection_id: string;
  /** Number of cells in the selection. */
  n_cells: number;
}

/**
 * Request body for selection stats / marker computation (CONTRACT 6.4).
 *
 * Exactly one of `selection_id` or `indices` must be provided.
 */
export interface SelectionStatsRequest {
  /** Registered selection id, if referencing a server-side selection. */
  selection_id?: string | null;
  /** Explicit cell indices, used when `selection_id` is absent. */
  indices?: number[] | null;
  /** Number of marker genes to compute (default 25). */
  n_markers?: number;
  /** Which obs columns to summarize; null/omitted = server default. */
  obs_keys?: string[] | null;
}

/**
 * A single differential-expression marker gene (CONTRACT 6.5).
 */
export interface MarkerGene {
  /** Gene name. */
  name: string;
  /** Test statistic (e.g. Wilcoxon score). */
  score: number;
  /** Log2 fold-change of selection vs rest. */
  log2fc: number;
  /** Raw p-value. */
  pval: number;
  /** Adjusted p-value. */
  pval_adj: number;
  /** Fraction of selected cells expressing the gene (>0). */
  pct_in: number;
  /** Fraction of rest cells expressing the gene (>0). */
  pct_out: number;
}

/**
 * Summary of a categorical obs column over a selection (CONTRACT 6.5).
 */
export interface CategoricalSummary {
  /** Discriminator. */
  kind: "categorical";
  /** Count of cells per category label. */
  counts: Record<string, number>;
  /** The most frequent label. */
  top: string;
}

/**
 * Summary of a continuous obs column over a selection (CONTRACT 6.5).
 */
export interface ContinuousSummary {
  /** Discriminator. */
  kind: "continuous";
  /** Arithmetic mean. */
  mean: number;
  /** Median. */
  median: number;
  /** Minimum. */
  min: number;
  /** Maximum. */
  max: number;
  /** Standard deviation. */
  std: number;
}

/**
 * Per-column summary returned in {@link SelectionStatsResponse.obs_summary}.
 * The `kind` field discriminates the union (CONTRACT 6.5).
 */
export type ObsColumnSummary = CategoricalSummary | ContinuousSummary;

/**
 * Response of the selection stats / markers endpoint (CONTRACT 6.5).
 */
export interface SelectionStatsResponse {
  /** Number of cells in the selection. */
  n_cells: number;
  /** Fraction of the dataset selected (`n_cells / n_obs`). */
  fraction: number;
  /** Number of marker genes computed. */
  n_markers: number;
  /** Number of "rest" cells actually used after MARKER_REST_CAP sampling. */
  rest_cells_used: number;
  /** Ranked marker genes. */
  markers: MarkerGene[];
  /** Per-obs-column summaries keyed by column name. */
  obs_summary: Record<string, ObsColumnSummary>;
  /** Honest caveats (e.g. "rest subsampled to 50000"). */
  notes: string[];
}

/* -------------------------------------------------------------------------- */
/* Section 5 — WebSocket job protocol                                          */
/* -------------------------------------------------------------------------- */

/**
 * The two recompute job kinds supported over the WebSocket (CONTRACT section 5).
 */
export type JobType = "recluster" | "recompute_umap";

/**
 * Coarse, named pipeline steps used for UI labels (CONTRACT section 5).
 * Clients must tolerate unknown step strings, hence the open union.
 */
export type JobStep =
  | "subset"
  | "pca"
  | "neighbors"
  | "leiden"
  | "umap"
  | "finalize"
  | (string & {});

/**
 * Parameters for a `recluster` job (CONTRACT section 5).
 */
export interface ReclusterParams {
  /** Leiden resolution. */
  resolution: number;
  /** Number of neighbors for the kNN graph. */
  n_neighbors?: number;
  /** Number of principal components to use. */
  n_pcs?: number;
}

/**
 * Parameters for a `recompute_umap` job (CONTRACT section 5).
 */
export interface RecomputeUmapParams {
  /** Number of neighbors for the kNN graph. */
  n_neighbors: number;
  /** Minimum distance for UMAP layout. */
  min_dist?: number;
  /** Number of principal components to use. */
  n_pcs?: number;
}

/**
 * Union of accepted job parameter shapes.
 */
export type JobParams = ReclusterParams | RecomputeUmapParams | Record<string, number>;

/**
 * Client -> server "submit" frame (CONTRACT 6.6 / section 5).
 */
export interface JobSubmit {
  /** Action discriminator. */
  action: "submit";
  /** Which compute to run. */
  job_type: JobType;
  /** Dataset to operate on. */
  dataset_id: string;
  /** Selection (from POST /selection) the compute is scoped to. */
  selection_id: string;
  /** Job-type-specific parameters. */
  params: JobParams;
}

/**
 * Client -> server "cancel" frame (CONTRACT section 5).
 */
export interface JobCancel {
  /** Action discriminator. */
  action: "cancel";
  /** Identifier of the job to cancel. */
  job_id: string;
}

/**
 * Any client -> server WebSocket frame.
 */
export type ClientJobMessage = JobSubmit | JobCancel;

/**
 * Server -> client "accepted" message (CONTRACT section 5).
 */
export interface JobAccepted {
  /** Type discriminator. */
  type: "accepted";
  /** Identifier assigned to the job. */
  job_id: string;
  /** Which compute was accepted. */
  job_type: JobType;
}

/**
 * Server -> client "progress" message (CONTRACT section 5).
 */
export interface JobProgress {
  /** Type discriminator. */
  type: "progress";
  /** Identifier of the job. */
  job_id: string;
  /** Coarse pipeline step label. */
  step: JobStep;
  /** Progress fraction in [0, 1]. */
  progress: number;
  /** Human-readable status message. */
  message: string;
}

/**
 * Summary payload attached to a completed job (CONTRACT section 5).
 */
export interface JobSummary {
  /** Number of clusters produced (recluster). */
  n_clusters?: number;
  /** Number of cells involved. */
  n_cells?: number;
  /** Allow forward-compatible extra summary fields. */
  [key: string]: number | undefined;
}

/**
 * Server -> client "completed" message (CONTRACT section 5).
 */
export interface JobCompleted {
  /** Type discriminator. */
  type: "completed";
  /** Identifier of the job. */
  job_id: string;
  /** Which compute completed. */
  job_type: JobType;
  /** Path to download the binary result (CONTRACT section 4.8). */
  result_url: string;
  /** Result summary. */
  summary: JobSummary;
}

/**
 * Server -> client "error" message (CONTRACT section 5).
 *
 * Validation-path errors are emitted with `job_id` null (no job was ever
 * accepted), so `job_id` mirrors the backend as `string | null`.
 */
export interface JobErrorMessage {
  /** Type discriminator. */
  type: "error";
  /** Identifier of the job, or null for pre-acceptance validation errors. */
  job_id: string | null;
  /** Human-readable error message. */
  error: string;
}

/**
 * Server -> client "cancelled" message (CONTRACT section 5).
 *
 * The backend always sends a concrete `job_id` here, but the inbound guard in
 * `api/jobs.ts` is defensively permissive, so the type allows `string | null`
 * to stay consistent with that guard (and with {@link JobErrorMessage}).
 */
export interface JobCancelled {
  /** Type discriminator. */
  type: "cancelled";
  /** Identifier of the job (null only defensively, for malformed frames). */
  job_id: string | null;
}

/**
 * Discriminated union of all server -> client WebSocket messages, keyed on
 * `type` (CONTRACT section 5).
 */
export type ServerJobMessage =
  | JobAccepted
  | JobProgress
  | JobCompleted
  | JobErrorMessage
  | JobCancelled;

/* -------------------------------------------------------------------------- */
/* Sections 4 & 7 — protocol + store helper types                             */
/* -------------------------------------------------------------------------- */

/**
 * Axis-aligned bounds of an embedding: `[minX, minY, maxX, maxY]`
 * (CONTRACT section 4.5 `X-Cellscope-Bounds`; store field `bounds`).
 */
export type Bounds = [number, number, number, number];

/**
 * Active coloring mode for the embedding (CONTRACT section 7 store shape).
 *
 * Discriminated on `type`.
 */
export type ColorMode =
  | { type: "none" }
  | { type: "gene"; gene: string }
  | { type: "obs"; column: string };

/**
 * Resolved kind of the active coloring (CONTRACT section 7 `colorKind`).
 */
export type ColorKind = "none" | "continuous" | "categorical";

/**
 * The interactive selection tool currently armed in the viewport (CONTRACT
 * section 7 store shape). `pan` lets deck.gl own the drag gesture; `box` and
 * `lasso` arm the SVG selection overlay.
 */
export type SelectionTool = "pan" | "box" | "lasso";
