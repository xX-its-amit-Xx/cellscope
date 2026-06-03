// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * RecomputePanel — re-run clustering / embedding on the current selection.
 *
 * Exposes a Leiden re-cluster (resolution slider) and a UMAP recompute
 * (n_neighbors, min_dist). Both operate on the active selection and are
 * disabled when nothing is selected. A progress bar reflects `store.job`
 * (step + progress + status); a cancel button is shown while a job runs.
 *
 * Job submission is delegated to `store.submitRecluster` / `store.submitUmap`,
 * which own the WebSocket plumbing (§5).
 */

import { useState } from 'react';
import { useStore } from '../store/useStore';

/** Default Leiden resolution. */
const DEFAULT_RESOLUTION = 1.0;
/** Default UMAP neighbor count. */
const DEFAULT_N_NEIGHBORS = 15;
/** Default UMAP minimum distance. */
const DEFAULT_MIN_DIST = 0.5;

/**
 * RecomputePanel. Holds transient form state locally; reads selection size
 * and job progress from the store.
 *
 * @returns The recompute panel element.
 */
export function RecomputePanel(): JSX.Element {
  const dataset = useStore((s) => s.dataset);
  const selection = useStore((s) => s.selection);
  const job = useStore((s) => s.job);
  const submitRecluster = useStore((s) => s.submitRecluster);
  const submitUmap = useStore((s) => s.submitUmap);

  const [resolution, setResolution] = useState(DEFAULT_RESOLUTION);
  const [nNeighbors, setNNeighbors] = useState(DEFAULT_N_NEIGHBORS);
  const [minDist, setMinDist] = useState(DEFAULT_MIN_DIST);

  const nSelected = selection?.length ?? 0;
  const hasSelection = nSelected > 0;
  const running = job?.status === 'running';
  const disabled = !dataset || !hasSelection || running;

  return (
    <div className="flex flex-col gap-3 text-sm text-slate-200">
      <p className="text-xs text-slate-400">
        {hasSelection ? (
          <>
            Recompute on{' '}
            <span className="font-medium text-slate-200">
              {nSelected.toLocaleString('en-US')}
            </span>{' '}
            selected cells.
          </>
        ) : (
          'Select cells to enable recompute.'
        )}
      </p>

      <section className="flex flex-col gap-2 rounded border border-slate-700 bg-slate-800/40 p-2.5">
        <span className="text-xs font-medium text-slate-300">
          Leiden re-cluster
        </span>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          <span className="flex justify-between">
            <span>Resolution</span>
            <span className="font-mono text-slate-200">
              {resolution.toFixed(2)}
            </span>
          </span>
          <input
            type="range"
            min={0.1}
            max={3}
            step={0.1}
            value={resolution}
            onChange={(e) => setResolution(Number(e.target.value))}
            disabled={disabled}
            className="accent-sky-500 disabled:opacity-40"
          />
        </label>
        <button
          type="button"
          disabled={disabled}
          onClick={() => submitRecluster({ resolution })}
          className="rounded bg-sky-600 px-2.5 py-1.5 text-xs font-medium text-white transition-colors hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Re-cluster
        </button>
      </section>

      <section className="flex flex-col gap-2 rounded border border-slate-700 bg-slate-800/40 p-2.5">
        <span className="text-xs font-medium text-slate-300">
          Recompute UMAP
        </span>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          <span className="flex justify-between">
            <span>n_neighbors</span>
            <span className="font-mono text-slate-200">{nNeighbors}</span>
          </span>
          <input
            type="range"
            min={2}
            max={100}
            step={1}
            value={nNeighbors}
            onChange={(e) => setNNeighbors(Number(e.target.value))}
            disabled={disabled}
            className="accent-sky-500 disabled:opacity-40"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          <span className="flex justify-between">
            <span>min_dist</span>
            <span className="font-mono text-slate-200">
              {minDist.toFixed(2)}
            </span>
          </span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={minDist}
            onChange={(e) => setMinDist(Number(e.target.value))}
            disabled={disabled}
            className="accent-sky-500 disabled:opacity-40"
          />
        </label>
        <button
          type="button"
          disabled={disabled}
          onClick={() =>
            submitUmap({ n_neighbors: nNeighbors, min_dist: minDist })
          }
          className="rounded bg-sky-600 px-2.5 py-1.5 text-xs font-medium text-white transition-colors hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Recompute UMAP
        </button>
      </section>

      {job && <JobProgress />}
    </div>
  );
}

/**
 * Render the active job's progress bar, step label, and a cancel control
 * while running. Reads `store.job` and `store.cancelJob`.
 *
 * @returns The job progress element, or null when no job exists.
 */
function JobProgress(): JSX.Element | null {
  const job = useStore((s) => s.job);
  const cancelJob = useStore((s) => s.cancelJob);

  if (!job) {
    return null;
  }

  const pct = Math.round(Math.max(0, Math.min(1, job.progress)) * 100);
  const running = job.status === 'running';

  const statusColor =
    job.status === 'error'
      ? 'text-rose-400'
      : job.status === 'done'
        ? 'text-emerald-400'
        : 'text-sky-400';

  const barColor =
    job.status === 'error'
      ? 'bg-rose-500'
      : job.status === 'done'
        ? 'bg-emerald-500'
        : 'bg-sky-500';

  return (
    <section className="flex flex-col gap-1.5 rounded border border-slate-700 bg-slate-900 p-2.5">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-slate-300">{job.type}</span>
        <span className={statusColor}>{job.status}</span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-700">
        <div
          className={`h-full rounded-full transition-all ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex items-center justify-between text-[10px] text-slate-400">
        <span className="truncate">{job.step || '—'}</span>
        <span className="font-mono">{pct}%</span>
      </div>
      {running && (
        <button
          type="button"
          onClick={() => cancelJob()}
          className="self-start rounded border border-rose-700 px-2 py-0.5 text-xs text-rose-300 transition-colors hover:bg-rose-950/50"
        >
          Cancel
        </button>
      )}
    </section>
  );
}

export default RecomputePanel;
