// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * Zustand store for CellScope (CONTRACT section 7).
 *
 * Holds the single source of client-side truth: the loaded dataset, the active
 * embedding positions/bounds, the current coloring (gene / obs / none), the
 * lasso/box selection and its server-side stats, the in-flight recompute job and
 * its results, plus view-level toggles (downsampling, colormap, hover).
 *
 * All network access is delegated to the REST client ({@link module:api/client})
 * and the WebSocket {@link JobClient}; this module owns no protocol/binary
 * parsing and never imports deck.gl. The derived deck.gl color buffer lives in
 * `useColorBuffer.ts`, not here (CONTRACT section 7).
 *
 * State updates are kept consistent: switching embedding clears recomputed
 * overlays, switching color clears the now-stale color fields, and mutating the
 * selection clears the stale `selectionId`/`selectionStats`.
 */

import { create } from "zustand";

import * as client from "../api/client";
import { ApiError } from "../api/client";
import { JobClient } from "../api/jobs";
import type {
  Bounds,
  ColorKind,
  ColorMode,
  DatasetInfo,
  JobType,
  ReclusterParams,
  RecomputeUmapParams,
  SelectionStatsResponse,
  SelectionTool,
  ServerJobMessage,
} from "../types";

/** Default colormap applied to continuous colorings (CONTRACT section 7). */
const DEFAULT_COLORMAP = "viridis";

/** Default target rendered count when downsampling is enabled (CONTRACT 7). */
const DEFAULT_DOWNSAMPLE_N = 1_000_000;

/**
 * In-flight (or just-finished) recompute job descriptor (CONTRACT section 7).
 *
 * `status` collapses the WebSocket lifecycle into the three states the UI
 * cares about; `step`/`progress` mirror the latest `progress` frame.
 */
export interface JobState {
  /** Server-assigned job id. */
  id: string;
  /** Which compute is running (`recluster` | `recompute_umap`). */
  type: string;
  /** Latest progress fraction in `[0, 1]`. */
  progress: number;
  /** Latest coarse pipeline step label. */
  step: string;
  /** Collapsed lifecycle status. */
  status: "running" | "done" | "error";
}

/** Hover descriptor: the index of the cell under the pointer (CONTRACT 7). */
export interface HoverState {
  /** Index into the full dataset of the hovered cell. */
  index: number;
}

/**
 * The reactive state slice of the store (CONTRACT section 7 fields, verbatim
 * names). Actions are defined separately in {@link StoreActions}.
 */
export interface StoreState {
  /** Currently loaded dataset metadata, or null before any load. */
  dataset: DatasetInfo | null;
  /** Whether a dataset load/upload is in flight. */
  loading: boolean;
  /** Last error message surfaced to the user, or null. */
  error: string | null;

  /** Active embedding obsm key (e.g. `X_umap`), or null. */
  embeddingKey: string | null;
  /** Interleaved xy coordinates, length `2 * nObs`, or null. */
  positions: Float32Array | null;
  /** Embedding bounds `[minX, minY, maxX, maxY]`, or null. */
  bounds: Bounds | null;
  /** Number of observations (cells) in the active embedding. */
  nObs: number;

  /** Active coloring mode (discriminated on `type`). */
  colorMode: ColorMode;
  /** Continuous per-cell values, or null when not coloring continuously. */
  colorValues: Float32Array | null;
  /** Categorical per-cell codes, or null when not coloring categorically. */
  colorCodes: Int32Array | null;
  /** Resolved kind of the active coloring. */
  colorKind: ColorKind;
  /** Value domain `[min, max]` for continuous colorings, or null. */
  colorDomain: [number, number] | null;
  /** Ordered category labels indexed by `colorCodes`, or null. */
  categories: string[] | null;
  /** Name of the active colormap (e.g. `"viridis"`). */
  colormapName: string;

  /** Selected cell indices into the FULL dataset, or null. */
  selection: Int32Array | null;
  /** Server-side selection id (from POST /selection), or null. */
  selectionId: string | null;
  /** Computed selection stats / markers, or null. */
  selectionStats: SelectionStatsResponse | null;

  /** In-flight or finished recompute job, or null. */
  job: JobState | null;
  /** Recluster labels (selection order), applied as ephemeral coloring. */
  reclusterLabels: Int32Array | null;
  /** Recomputed UMAP coords (interleaved xy) for the selection, or null. */
  recomputedPositions: Float32Array | null;

  /** Whether downsampling is enabled for rendering. */
  downsample: boolean;
  /** Target rendered count when downsampling. */
  downsampleN: number;
  /** Cell currently under the pointer, or null. */
  hovered: HoverState | null;

  /** The selection tool currently armed in the viewport. */
  activeTool: SelectionTool;
}

/**
 * The imperative action surface of the store (CONTRACT section 7).
 *
 * Async actions resolve once their effect has been applied to the store; they
 * never throw, instead routing failures into the `error` field (for loads) or
 * the `job.status` (for recompute jobs).
 */
export interface StoreActions {
  /**
   * Load a dataset by a server-side `.h5ad` path, then auto-select its default
   * embedding (CONTRACT sections 4.1, 4.5).
   *
   * @param path - Absolute or `DATA_DIR`-relative path to the `.h5ad` file.
   * @returns A promise that resolves once the dataset (and embedding) are set,
   *   or the failure has been recorded in `error`.
   */
  loadByPath: (path: string) => Promise<void>;

  /**
   * Upload an `.h5ad` file, load it, then auto-select its default embedding
   * (CONTRACT sections 4.2, 4.5).
   *
   * @param file - The `.h5ad` file selected by the user.
   * @returns A promise that resolves once the dataset (and embedding) are set,
   *   or the failure has been recorded in `error`.
   */
  uploadFile: (file: File) => Promise<void>;

  /**
   * Fetch and activate an embedding, updating positions/bounds/nObs and
   * clearing any recomputed overlay (CONTRACT section 4.5).
   *
   * @param key - Embedding obsm key; omit to use the dataset default.
   * @returns A promise resolving once the embedding is applied.
   */
  setEmbedding: (key?: string) => Promise<void>;

  /**
   * Color the embedding by a gene's expression (continuous) (CONTRACT 4.6).
   *
   * @param gene - A var_name or stringified integer var index.
   * @returns A promise resolving once the coloring is applied.
   */
  colorByGene: (gene: string) => Promise<void>;

  /**
   * Color the embedding by an obs column, choosing categorical or continuous
   * coloring from the response kind (CONTRACT section 4.6).
   *
   * @param column - The obs column name.
   * @returns A promise resolving once the coloring is applied.
   */
  colorByObs: (column: string) => Promise<void>;

  /**
   * Reset all coloring back to `none`.
   */
  clearColor: () => void;

  /**
   * Set the active selection and register it server-side, storing the returned
   * `selectionId` and clearing any stale stats (CONTRACT section 4.7).
   *
   * @param indices - Selected cell indices into the full dataset.
   * @returns A promise resolving once the selection is registered (or the
   *   failure recorded in `error`).
   */
  setSelection: (indices: Int32Array) => Promise<void>;

  /**
   * Compute summary statistics and marker genes for the active selection
   * (CONTRACT section 4.7).
   *
   * @param nMarkers - Optional number of marker genes to compute.
   * @returns A promise resolving once stats are stored (or the failure
   *   recorded in `error`).
   */
  fetchSelectionStats: (nMarkers?: number) => Promise<void>;

  /**
   * Submit a `recluster` job over the active selection, wiring progress and
   * completion into the `job`/`reclusterLabels` state (CONTRACT section 5).
   *
   * @param params - Leiden resolution and optional graph parameters.
   * @returns A promise resolving once the submit frame is sent (or a failure
   *   recorded in `job.status`/`error`).
   */
  submitRecluster: (params: ReclusterParams) => Promise<void>;

  /**
   * Submit a `recompute_umap` job over the active selection, wiring progress
   * and completion into the `job`/`recomputedPositions` state (CONTRACT 5).
   *
   * @param params - UMAP neighbors and optional layout parameters.
   * @returns A promise resolving once the submit frame is sent (or a failure
   *   recorded in `job.status`/`error`).
   */
  submitUmap: (params: RecomputeUmapParams) => Promise<void>;

  /**
   * Toggle render downsampling.
   *
   * @param on - Whether downsampling should be enabled.
   */
  setDownsample: (on: boolean) => void;

  /**
   * Set the active colormap by name.
   *
   * @param name - Colormap name (e.g. `"viridis"`).
   */
  setColormap: (name: string) => void;

  /**
   * Set (or clear) the hovered cell.
   *
   * @param h - The hover descriptor, or null to clear.
   */
  setHovered: (h: HoverState | null) => void;

  /**
   * Clear the active selection, its server id, stats and recompute overlays.
   */
  clearSelection: () => void;

  /**
   * Arm a selection tool in the viewport (`pan` | `box` | `lasso`).
   *
   * @param tool - The tool to activate.
   */
  setActiveTool: (tool: SelectionTool) => void;

  /**
   * Request cancellation of the in-flight recompute job, if any (CONTRACT
   * section 5). Sends the cancel frame over the shared WebSocket job client;
   * the store's `cancelled` frame handler finalizes the job state.
   */
  cancelJob: () => void;

  /**
   * Clear the current `error` message.
   */
  clearError: () => void;
}

/**
 * Full store type combining reactive state and actions (CONTRACT section 7).
 */
export type Store = StoreState & StoreActions;

/** Initial reactive state, also used to reset on a fresh dataset load. */
const INITIAL_STATE: StoreState = {
  dataset: null,
  loading: false,
  error: null,

  embeddingKey: null,
  positions: null,
  bounds: null,
  nObs: 0,

  colorMode: { type: "none" },
  colorValues: null,
  colorCodes: null,
  colorKind: "none",
  colorDomain: null,
  categories: null,
  colormapName: DEFAULT_COLORMAP,

  selection: null,
  selectionId: null,
  selectionStats: null,

  job: null,
  reclusterLabels: null,
  recomputedPositions: null,

  downsample: false,
  downsampleN: DEFAULT_DOWNSAMPLE_N,
  hovered: null,

  activeTool: "pan",
};

/**
 * Convert an arbitrary thrown value into a human-readable message, preferring
 * the {@link ApiError} message (which already carries the FastAPI `detail`).
 *
 * @param err - The caught value.
 * @returns A user-facing error string.
 */
function toMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return String(err);
}

/**
 * Lazily-created shared WebSocket job client. Created on first recompute submit
 * and reused (and reconnected on demand) for subsequent jobs, so subscriptions
 * survive across submissions.
 */
let jobClient: JobClient | null = null;

/**
 * Get the process-wide {@link JobClient}, constructing it on first use.
 *
 * @returns The shared job client instance.
 */
function getJobClient(): JobClient {
  if (jobClient === null) {
    jobClient = new JobClient();
  }
  return jobClient;
}

/**
 * The CellScope Zustand store hook.
 *
 * Components subscribe with a selector, e.g.
 * `const positions = useStore((s) => s.positions);`. Actions are stable
 * references on the store and may be pulled out once and reused.
 *
 * @returns The reactive store selector hook with bound actions.
 */
export const useStore = create<Store>((set, get) => {
  /**
   * Drive a recompute job to completion: subscribe to the shared job client,
   * submit the job, and translate server frames into store updates. On a
   * `completed` frame the binary result is fetched and applied; the
   * subscription is then torn down. Errors are routed into `job.status` and
   * `error`.
   *
   * @param jobType - Which compute to run.
   * @param params - Job-type-specific parameters.
   */
  const runJob = async (
    jobType: JobType,
    params: ReclusterParams | RecomputeUmapParams,
  ): Promise<void> => {
    const state = get();
    const dataset = state.dataset;
    if (dataset === null) {
      set({ error: "No dataset loaded" });
      return;
    }
    const selectionId = state.selectionId;
    if (selectionId === null) {
      set({ error: "No registered selection to recompute" });
      return;
    }

    const cli = getJobClient();

    // A single subscription owns this job's lifecycle; it unsubscribes itself
    // once the job reaches a terminal state (completed / error / cancelled).
    let unsubscribe: (() => void) | null = null;

    const finish = (): void => {
      if (unsubscribe !== null) {
        unsubscribe();
        unsubscribe = null;
      }
    };

    const handle = (message: ServerJobMessage): void => {
      switch (message.type) {
        case "accepted": {
          set({
            job: {
              id: message.job_id,
              type: message.job_type,
              progress: 0,
              step: "subset",
              status: "running",
            },
          });
          break;
        }
        case "progress": {
          const job = get().job;
          if (job === null || job.id !== message.job_id) {
            break;
          }
          set({
            job: {
              ...job,
              progress: message.progress,
              step: message.step,
              status: "running",
            },
          });
          break;
        }
        case "completed": {
          finish();
          void applyJobResult(message.job_id, message.job_type);
          break;
        }
        case "error": {
          finish();
          const job = get().job;
          set({
            job:
              job !== null && job.id === message.job_id
                ? { ...job, status: "error" }
                : {
                    // Validation-path errors carry a null `job_id`; attribute
                    // them to the seeded job (empty id) rather than dropping.
                    id: message.job_id ?? "",
                    type: jobType,
                    progress: 0,
                    step: "finalize",
                    status: "error",
                  },
            error: message.error,
          });
          break;
        }
        case "cancelled": {
          finish();
          const job = get().job;
          if (job !== null && job.id === message.job_id) {
            set({ job: null });
          }
          break;
        }
        default:
          break;
      }
    };

    /**
     * Fetch and apply a finished job's binary result.
     *
     * @param jobId - The completed job id.
     * @param completedType - The job type reported in the completed frame.
     */
    const applyJobResult = async (
      jobId: string,
      completedType: JobType,
    ): Promise<void> => {
      try {
        const result = await client.getJobResult(jobId, completedType);
        const job = get().job;
        const markDone = (): JobState | null =>
          job !== null && job.id === jobId
            ? { ...job, progress: 1, step: "finalize", status: "done" }
            : job;
        if (result.jobType === "recluster") {
          set({
            reclusterLabels: result.labels,
            recomputedPositions: null,
            job: markDone(),
          });
        } else {
          set({
            recomputedPositions: result.positions,
            reclusterLabels: null,
            job: markDone(),
          });
        }
      } catch (err) {
        const job = get().job;
        set({
          job:
            job !== null && job.id === jobId
              ? { ...job, status: "error" }
              : job,
          error: toMessage(err),
        });
      }
    };

    // Seed an initial running job state so the UI reacts immediately, before
    // the `accepted` frame arrives.
    set({
      job: {
        id: "",
        type: jobType,
        progress: 0,
        step: "subset",
        status: "running",
      },
      error: null,
      reclusterLabels: null,
      recomputedPositions: null,
    });

    try {
      unsubscribe = cli.onMessage(handle);
      await cli.connect();
      await cli.submit(jobType, dataset.dataset_id, selectionId, params);
    } catch (err) {
      finish();
      const job = get().job;
      set({
        job: job !== null ? { ...job, status: "error" } : null,
        error: toMessage(err),
      });
    }
  };

  return {
    ...INITIAL_STATE,

    loadByPath: async (path: string): Promise<void> => {
      set({ loading: true, error: null });
      try {
        const dataset = await client.loadByPath(path);
        set({
          ...INITIAL_STATE,
          dataset,
          loading: false,
        });
        await get().setEmbedding(dataset.default_embedding ?? undefined);
      } catch (err) {
        set({ loading: false, error: toMessage(err) });
      }
    },

    uploadFile: async (file: File): Promise<void> => {
      set({ loading: true, error: null });
      try {
        const dataset = await client.uploadFile(file);
        set({
          ...INITIAL_STATE,
          dataset,
          loading: false,
        });
        await get().setEmbedding(dataset.default_embedding ?? undefined);
      } catch (err) {
        set({ loading: false, error: toMessage(err) });
      }
    },

    setEmbedding: async (key?: string): Promise<void> => {
      const dataset = get().dataset;
      if (dataset === null) {
        set({ error: "No dataset loaded" });
        return;
      }
      const resolvedKey = key ?? dataset.default_embedding;
      if (resolvedKey === null || resolvedKey === undefined) {
        set({ error: "No embedding available for this dataset" });
        return;
      }
      try {
        const { positions, bounds, nObs } = await client.getEmbedding(
          dataset.dataset_id,
          resolvedKey,
        );
        set({
          embeddingKey: resolvedKey,
          positions,
          bounds,
          nObs,
          recomputedPositions: null,
          error: null,
        });
      } catch (err) {
        set({ error: toMessage(err) });
      }
    },

    colorByGene: async (gene: string): Promise<void> => {
      const dataset = get().dataset;
      if (dataset === null) {
        set({ error: "No dataset loaded" });
        return;
      }
      try {
        const { values, min, max, gene: resolved } = await client.getExpression(
          dataset.dataset_id,
          gene,
        );
        set({
          colorMode: { type: "gene", gene: resolved },
          colorValues: values,
          colorKind: "continuous",
          colorDomain: [min, max],
          categories: null,
          colorCodes: null,
          error: null,
        });
      } catch (err) {
        set({ error: toMessage(err) });
      }
    },

    colorByObs: async (column: string): Promise<void> => {
      const dataset = get().dataset;
      if (dataset === null) {
        set({ error: "No dataset loaded" });
        return;
      }
      try {
        const result = await client.getObs(dataset.dataset_id, column);
        if (result.kind === "categorical" && result.codes !== undefined) {
          // Prefer the category labels already delivered in DatasetInfo at load;
          // fall back to synthesizing labels from the code count if absent.
          const fromInfo = dataset.obs_columns.find(
            (c) => c.name === column,
          )?.categories;
          const nCategories =
            result.nCategories ?? (fromInfo !== undefined ? fromInfo.length : 0);
          const categories =
            fromInfo ??
            Array.from({ length: nCategories }, (_, i) => String(i));
          set({
            colorMode: { type: "obs", column },
            colorCodes: result.codes,
            categories,
            colorKind: "categorical",
            colorValues: null,
            colorDomain: null,
            error: null,
          });
        } else if (result.kind === "continuous" && result.values !== undefined) {
          set({
            colorMode: { type: "obs", column },
            colorValues: result.values,
            colorKind: "continuous",
            colorDomain: [result.min ?? 0, result.max ?? 0],
            categories: null,
            colorCodes: null,
            error: null,
          });
        } else {
          set({ error: `Malformed obs response for column "${column}"` });
        }
      } catch (err) {
        set({ error: toMessage(err) });
      }
    },

    clearColor: (): void => {
      set({
        colorMode: { type: "none" },
        colorValues: null,
        colorCodes: null,
        colorKind: "none",
        colorDomain: null,
        categories: null,
      });
    },

    setSelection: async (indices: Int32Array): Promise<void> => {
      const dataset = get().dataset;
      // Apply the selection immediately and invalidate stale derived state.
      set({
        selection: indices,
        selectionId: null,
        selectionStats: null,
      });
      if (dataset === null) {
        set({ error: "No dataset loaded" });
        return;
      }
      try {
        const ref = await client.registerSelection(
          dataset.dataset_id,
          indices,
        );
        // Guard against a newer selection having replaced this one mid-flight.
        if (get().selection === indices) {
          set({ selectionId: ref.selection_id, error: null });
        }
      } catch (err) {
        set({ error: toMessage(err) });
      }
    },

    fetchSelectionStats: async (nMarkers?: number): Promise<void> => {
      const state = get();
      const dataset = state.dataset;
      if (dataset === null) {
        set({ error: "No dataset loaded" });
        return;
      }
      const selectionId = state.selectionId;
      const selection = state.selection;
      if (selectionId === null && selection === null) {
        set({ error: "No selection to summarize" });
        return;
      }
      try {
        const stats = await client.selectionStats(dataset.dataset_id, {
          selection_id: selectionId ?? undefined,
          indices:
            selectionId === null && selection !== null
              ? Array.from(selection)
              : undefined,
          n_markers: nMarkers,
        });
        set({ selectionStats: stats, error: null });
      } catch (err) {
        set({ error: toMessage(err) });
      }
    },

    submitRecluster: async (params: ReclusterParams): Promise<void> => {
      await runJob("recluster", params);
    },

    submitUmap: async (params: RecomputeUmapParams): Promise<void> => {
      await runJob("recompute_umap", params);
    },

    setDownsample: (on: boolean): void => {
      set({ downsample: on });
    },

    setColormap: (name: string): void => {
      set({ colormapName: name });
    },

    setHovered: (h: HoverState | null): void => {
      set({ hovered: h });
    },

    clearSelection: (): void => {
      set({
        selection: null,
        selectionId: null,
        selectionStats: null,
        reclusterLabels: null,
        recomputedPositions: null,
        job: null,
      });
    },

    setActiveTool: (tool: SelectionTool): void => {
      set({ activeTool: tool });
    },

    cancelJob: (): void => {
      const jobId = get().job?.id;
      // Nothing to cancel unless a job with a server-assigned id is in flight.
      if (jobId === undefined || jobId === "") {
        return;
      }
      // Best-effort cancel over the shared WebSocket; the `cancelled` frame
      // handler in runJob finalizes the store job state.
      void getJobClient().cancel(jobId);
    },

    clearError: (): void => {
      set({ error: null });
    },
  };
});
