// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * REST client for the CellScope API (CONTRACT section 4).
 *
 * Uses the Fetch API against the same origin. All paths are under the `/api`
 * prefix. JSON responses are parsed directly; binary responses are read via
 * `res.arrayBuffer()` and decoded with the helpers in {@link module:api/binary},
 * with `X-Cellscope-*` headers read by their exact contract names.
 *
 * On a non-2xx response, {@link ApiError} is thrown carrying the FastAPI
 * `{ "detail": "..." }` message when present.
 */

import {
  assertNObs,
  getHeaderFloat,
  getHeaderInt,
  getHeaderString,
  parseBounds,
  parseFloat32,
  parseInt32,
  encodeInt32,
} from "./binary";
import type {
  Bounds,
  DatasetInfo,
  DatasetSummary,
  GeneHit,
  GeneSearchResponse,
  JobType,
  ObsColumnKind,
  SelectionRef,
  SelectionStatsRequest,
  SelectionStatsResponse,
} from "../types";

/** Base URL for the API. Empty string targets the current origin. */
export const API_BASE = "";

/** Prefix under which all API routes live (CONTRACT section 4). */
const API_PREFIX = "/api";

/**
 * Error thrown for non-2xx API responses. Carries the HTTP status and, when the
 * body is JSON, the FastAPI `detail` message.
 */
export class ApiError extends Error {
  /** HTTP status code of the failed response. */
  public readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Build a fully-qualified API URL from a route path and optional query params.
 *
 * @param path - Route path beginning with `/` (without the `/api` prefix).
 * @param query - Optional query parameters; `undefined` values are skipped.
 * @returns The absolute-or-origin-relative URL string.
 */
function apiUrl(
  path: string,
  query?: Record<string, string | number | undefined>,
): string {
  let url = `${API_BASE}${API_PREFIX}${path}`;
  if (query) {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) {
        search.set(key, String(value));
      }
    }
    const qs = search.toString();
    if (qs) {
      url += `?${qs}`;
    }
  }
  return url;
}

/**
 * Extract a human-readable error message from a failed response, preferring the
 * FastAPI `detail` field when the body is JSON.
 *
 * @param res - The non-ok response.
 * @returns A message string for the thrown {@link ApiError}.
 */
async function errorMessage(res: Response): Promise<string> {
  try {
    const data: unknown = await res.json();
    if (data && typeof data === "object" && "detail" in data) {
      const detail = (data as { detail: unknown }).detail;
      if (typeof detail === "string") {
        return detail;
      }
      return JSON.stringify(detail);
    }
    return JSON.stringify(data);
  } catch {
    return `${res.status} ${res.statusText}`;
  }
}

/**
 * Throw an {@link ApiError} if the response is not ok.
 *
 * @param res - The response to check.
 * @throws {@link ApiError} when `res.ok` is false.
 */
async function ensureOk(res: Response): Promise<void> {
  if (!res.ok) {
    throw new ApiError(res.status, await errorMessage(res));
  }
}

/**
 * Perform a request expecting a JSON response.
 *
 * @typeParam T - Expected shape of the parsed JSON.
 * @param url - Request URL.
 * @param init - Optional fetch init.
 * @returns The parsed JSON body.
 * @throws {@link ApiError} on a non-2xx response.
 */
async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  await ensureOk(res);
  return (await res.json()) as T;
}

/**
 * Perform a request expecting a binary response, returning both the decoded
 * bytes and the response headers.
 *
 * @param url - Request URL.
 * @param init - Optional fetch init.
 * @returns The body as an `ArrayBuffer` plus the response `Headers`.
 * @throws {@link ApiError} on a non-2xx response.
 */
async function fetchBinary(
  url: string,
  init?: RequestInit,
): Promise<{ buffer: ArrayBuffer; headers: Headers }> {
  const res = await fetch(url, init);
  await ensureOk(res);
  const buffer = await res.arrayBuffer();
  return { buffer, headers: res.headers };
}

/* -------------------------------------------------------------------------- */
/* Datasets (CONTRACT 4.1–4.5)                                                 */
/* -------------------------------------------------------------------------- */

/**
 * Load a dataset by a server-side `.h5ad` path (CONTRACT section 4.1).
 *
 * @param path - Absolute or `DATA_DIR`-relative path to the `.h5ad` file.
 * @returns Metadata for the newly loaded dataset.
 * @throws {@link ApiError} on bad path (400), not found (404), wrong type (415).
 */
export async function loadByPath(path: string): Promise<DatasetInfo> {
  return fetchJson<DatasetInfo>(apiUrl("/datasets/load"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
}

/**
 * Upload an `.h5ad` file via multipart form data and load it
 * (CONTRACT section 4.2).
 *
 * @param file - The `.h5ad` file selected by the user.
 * @returns Metadata for the newly loaded dataset.
 * @throws {@link ApiError} when over the upload size limit (413), etc.
 */
export async function uploadFile(file: File): Promise<DatasetInfo> {
  const form = new FormData();
  form.append("file", file);
  return fetchJson<DatasetInfo>(apiUrl("/datasets/upload"), {
    method: "POST",
    body: form,
  });
}

/**
 * Response of the dataset-listing endpoint (CONTRACT section 4.3).
 */
export interface DatasetListResponse {
  /** Currently loaded datasets. */
  loaded: DatasetSummary[];
  /** `.h5ad` filenames discoverable in `DATA_DIR` (not necessarily loaded). */
  available_files: string[];
}

/**
 * List loaded datasets and discoverable files (CONTRACT section 4.3).
 *
 * @returns The loaded datasets and available filenames.
 * @throws {@link ApiError} on a non-2xx response.
 */
export async function listDatasets(): Promise<DatasetListResponse> {
  return fetchJson<DatasetListResponse>(apiUrl("/datasets"));
}

/**
 * Fetch metadata for a loaded dataset (CONTRACT section 4.4).
 *
 * @param id - The dataset id.
 * @returns The dataset metadata.
 * @throws {@link ApiError} with status 404 if the dataset is unknown.
 */
export async function getDatasetInfo(id: string): Promise<DatasetInfo> {
  return fetchJson<DatasetInfo>(apiUrl(`/datasets/${id}`));
}

/**
 * Decoded embedding payload returned by {@link getEmbedding}.
 */
export interface EmbeddingResult {
  /** Interleaved xy coordinates, length `2 * nObs`. */
  positions: Float32Array;
  /** Axis-aligned data-space bounds `[minX, minY, maxX, maxY]`. */
  bounds: Bounds;
  /** Number of observations (cells). */
  nObs: number;
}

/**
 * Fetch an embedding as interleaved-xy Float32 coordinates
 * (CONTRACT section 4.5).
 *
 * Asserts that the decoded element count matches `X-Cellscope-N-Obs` and that
 * the body is exactly `8 * nObs` bytes (two Float32 per cell).
 *
 * @param id - The dataset id.
 * @param key - Embedding obsm key; omit to use the dataset's default embedding.
 * @returns The positions, bounds and observation count.
 * @throws {@link ApiError} (404 unknown dataset/key) or a protocol error.
 */
export async function getEmbedding(
  id: string,
  key?: string,
): Promise<EmbeddingResult> {
  const { buffer, headers } = await fetchBinary(
    apiUrl(`/datasets/${id}/embedding`, { key }),
  );
  const nObs = getHeaderInt(headers, "X-Cellscope-N-Obs");
  if (buffer.byteLength !== 8 * nObs) {
    throw new ApiError(
      502,
      `Embedding length ${buffer.byteLength} != 8 * n_obs (${8 * nObs})`,
    );
  }
  const positions = parseFloat32(buffer);
  assertNObs(positions.length / 2, nObs);
  const bounds = parseBounds(headers.get("X-Cellscope-Bounds"));
  return { positions, bounds, nObs };
}

/* -------------------------------------------------------------------------- */
/* Color sources (CONTRACT 4.6)                                                */
/* -------------------------------------------------------------------------- */

/**
 * Search genes by case-insensitive substring/prefix (CONTRACT section 4.6).
 *
 * @param id - The dataset id.
 * @param query - Search string; empty returns the first `limit` genes.
 * @param limit - Maximum number of hits to return.
 * @returns The matching gene hits and total match count.
 * @throws {@link ApiError} on a non-2xx response.
 */
export async function searchGenes(
  id: string,
  query: string,
  limit: number,
): Promise<GeneSearchResponse> {
  return fetchJson<GeneSearchResponse>(
    apiUrl(`/datasets/${id}/genes`, { query, limit }),
  );
}

/**
 * Decoded expression payload returned by {@link getExpression}.
 */
export interface ExpressionResult {
  /** Per-cell expression values, length `nObs`. */
  values: Float32Array;
  /** Minimum value (from `X-Cellscope-Min`). */
  min: number;
  /** Maximum value (from `X-Cellscope-Max`). */
  max: number;
  /** Resolved gene var_name (from `X-Cellscope-Gene`). */
  gene: string;
}

/**
 * Fetch per-cell expression for a gene as flat Float32 values
 * (CONTRACT section 4.6).
 *
 * @param id - The dataset id.
 * @param gene - A var_name or stringified integer var index.
 * @param layer - Optional layer name; defaults to `adata.X`.
 * @returns The values, value range and resolved gene name.
 * @throws {@link ApiError} with status 404 if the gene is not found.
 */
export async function getExpression(
  id: string,
  gene: string,
  layer?: string,
): Promise<ExpressionResult> {
  const { buffer, headers } = await fetchBinary(
    apiUrl(`/datasets/${id}/expression`, { gene, layer }),
  );
  const nObs = getHeaderInt(headers, "X-Cellscope-N-Obs");
  const values = parseFloat32(buffer);
  assertNObs(values.length, nObs);
  return {
    values,
    min: getHeaderFloat(headers, "X-Cellscope-Min"),
    max: getHeaderFloat(headers, "X-Cellscope-Max"),
    gene: getHeaderString(headers, "X-Cellscope-Gene"),
  };
}

/**
 * Decoded obs-column payload returned by {@link getObs}.
 *
 * The `kind` field discriminates: categorical columns carry Int32 `codes` and
 * `nCategories`; continuous columns carry Float32 `values` plus `min`/`max`.
 */
export interface ObsResult {
  /** Whether the column is categorical or continuous. */
  kind: ObsColumnKind;
  /** Int32 category codes (categorical only; code `-1` = missing). */
  codes?: Int32Array;
  /** Float32 per-cell values (continuous only; `NaN` allowed). */
  values?: Float32Array;
  /** Number of categories (categorical only). */
  nCategories?: number;
  /** Minimum value (continuous only). */
  min?: number;
  /** Maximum value (continuous only). */
  max?: number;
}

/**
 * Fetch an obs column, decoding either Int32 codes (categorical) or Float32
 * values (continuous) based on `X-Cellscope-Kind` (CONTRACT section 4.6).
 *
 * @param id - The dataset id.
 * @param column - The obs column name.
 * @returns The decoded column payload.
 * @throws {@link ApiError} on a non-2xx response, or a protocol error on an
 *   unexpected `X-Cellscope-Kind`.
 */
export async function getObs(id: string, column: string): Promise<ObsResult> {
  const { buffer, headers } = await fetchBinary(
    apiUrl(`/datasets/${id}/obs`, { column }),
  );
  const nObs = getHeaderInt(headers, "X-Cellscope-N-Obs");
  const kind = getHeaderString(headers, "X-Cellscope-Kind");
  if (kind === "categorical") {
    const codes = parseInt32(buffer);
    assertNObs(codes.length, nObs);
    return {
      kind: "categorical",
      codes,
      nCategories: getHeaderInt(headers, "X-Cellscope-N-Categories"),
    };
  }
  if (kind === "continuous") {
    const values = parseFloat32(buffer);
    assertNObs(values.length, nObs);
    return {
      kind: "continuous",
      values,
      min: getHeaderFloat(headers, "X-Cellscope-Min"),
      max: getHeaderFloat(headers, "X-Cellscope-Max"),
    };
  }
  throw new ApiError(502, `Unexpected X-Cellscope-Kind: "${kind}"`);
}

/* -------------------------------------------------------------------------- */
/* Selections (CONTRACT 4.7)                                                   */
/* -------------------------------------------------------------------------- */

/**
 * Register a selection of cell indices server-side (CONTRACT section 4.7).
 *
 * Sends the indices as a little-endian Int32 octet-stream body (preferred,
 * scales to millions of cells).
 *
 * @param id - The dataset id.
 * @param indices - Cell indices in `[0, n_obs)`.
 * @returns A reference to the registered selection.
 * @throws {@link ApiError} with status 400 on out-of-range indices.
 */
export async function registerSelection(
  id: string,
  indices: Int32Array,
): Promise<SelectionRef> {
  return fetchJson<SelectionRef>(apiUrl(`/datasets/${id}/selection`), {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: encodeInt32(indices),
  });
}

/**
 * Compute summary statistics and marker genes for a selection
 * (CONTRACT section 4.7).
 *
 * @param id - The dataset id.
 * @param req - The stats request (exactly one of `selection_id` / `indices`).
 * @returns The computed stats and markers.
 * @throws {@link ApiError} on a non-2xx response.
 */
export async function selectionStats(
  id: string,
  req: SelectionStatsRequest,
): Promise<SelectionStatsResponse> {
  return fetchJson<SelectionStatsResponse>(
    apiUrl(`/datasets/${id}/selection/stats`),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    },
  );
}

/* -------------------------------------------------------------------------- */
/* Job results (CONTRACT 4.8)                                                  */
/* -------------------------------------------------------------------------- */

/**
 * Result of a `recluster` job: a cluster label per selected cell, in selection
 * order (CONTRACT section 4.8).
 */
export interface ReclusterResult {
  /** Discriminator matching `X-Cellscope-Job-Type`. */
  jobType: "recluster";
  /** Int32 cluster labels, length `nSelected`, in selection order. */
  labels: Int32Array;
  /** Number of selected cells (from `X-Cellscope-N`). */
  nSelected: number;
  /** Number of distinct clusters (from `X-Cellscope-N-Clusters`). */
  nClusters: number;
}

/**
 * Result of a `recompute_umap` job: interleaved-xy coordinates per selected
 * cell (CONTRACT section 4.8).
 */
export interface RecomputeUmapResult {
  /** Discriminator matching `X-Cellscope-Job-Type`. */
  jobType: "recompute_umap";
  /** Interleaved xy coordinates, length `2 * nSelected`. */
  positions: Float32Array;
  /** Number of selected cells (from `X-Cellscope-N`). */
  nSelected: number;
  /** Axis-aligned bounds `[minX, minY, maxX, maxY]`. */
  bounds: Bounds;
}

/**
 * Union of decoded job results, discriminated on `jobType`.
 */
export type JobResult = ReclusterResult | RecomputeUmapResult;

/**
 * Download and decode a finished job result (CONTRACT section 4.8).
 *
 * For `recluster`, decodes Int32 cluster labels and reads `X-Cellscope-N` /
 * `X-Cellscope-N-Clusters`. For `recompute_umap`, decodes interleaved-xy
 * Float32 coordinates and reads `X-Cellscope-N` / `X-Cellscope-Bounds`.
 *
 * @param id - The job id.
 * @param jobType - The job type, used to select the decoder.
 * @returns The decoded result for the given job type.
 * @throws {@link ApiError} with status 404 if the job is unknown or unfinished,
 *   or a protocol error if the response header type disagrees.
 */
export async function getJobResult(
  id: string,
  jobType: JobType,
): Promise<JobResult> {
  const { buffer, headers } = await fetchBinary(apiUrl(`/jobs/${id}/result`));
  const headerType = getHeaderString(headers, "X-Cellscope-Job-Type");
  if (headerType !== jobType) {
    throw new ApiError(
      502,
      `Job-type mismatch: requested "${jobType}" but header is "${headerType}"`,
    );
  }
  const nSelected = getHeaderInt(headers, "X-Cellscope-N");
  if (jobType === "recluster") {
    const labels = parseInt32(buffer);
    assertNObs(labels.length, nSelected);
    return {
      jobType: "recluster",
      labels,
      nSelected,
      nClusters: getHeaderInt(headers, "X-Cellscope-N-Clusters"),
    };
  }
  const positions = parseFloat32(buffer);
  if (positions.length !== 2 * nSelected) {
    throw new ApiError(
      502,
      `recompute_umap length ${positions.length} != 2 * n (${2 * nSelected})`,
    );
  }
  return {
    jobType: "recompute_umap",
    positions,
    nSelected,
    bounds: parseBounds(headers.get("X-Cellscope-Bounds")),
  };
}

/* -------------------------------------------------------------------------- */
/* Health (CONTRACT 4.9)                                                       */
/* -------------------------------------------------------------------------- */

/**
 * Response of the health endpoint (CONTRACT section 4.9).
 */
export interface HealthResponse {
  /** Status string, `"ok"` when healthy. */
  status: string;
  /** Server version string. */
  version: string;
}

/**
 * Query server health (CONTRACT section 4.9).
 *
 * @returns The health status and version.
 * @throws {@link ApiError} on a non-2xx response.
 */
export async function getHealth(): Promise<HealthResponse> {
  return fetchJson<HealthResponse>(apiUrl("/health"));
}

/**
 * Re-export of {@link GeneHit} for callers importing only from the client.
 */
export type { GeneHit };
