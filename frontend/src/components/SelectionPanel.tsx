// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * SelectionPanel — summarize the currently selected subset of cells.
 *
 * Renders `store.selectionStats` (a {@link SelectionStatsResponse}): the cell
 * count and dataset fraction, a ranked table of marker genes
 * (name / log2fc / pval_adj / pct_in / pct_out), and a per-obs-column summary
 * (categorical counts or continuous descriptive statistics). A button drives
 * `store.fetchSelectionStats` (compute / refresh) and a second clears the
 * selection via `store.clearSelection`.
 *
 * The panel is honest about its state: it distinguishes "no selection", "have a
 * selection but stats not yet computed", and "stats present", and surfaces any
 * server-provided caveats (`notes`, e.g. the markers "rest" subsample cap).
 */

import { useState } from 'react';
import { useStore } from '../store/useStore';
import type {
  MarkerGene,
  ObsColumnSummary,
  SelectionStatsResponse,
} from '../types';

/**
 * Format an integer with locale thousands separators.
 *
 * @param n - The integer to format.
 * @returns A grouped string (e.g. `12,345`).
 */
function formatInt(n: number): string {
  return n.toLocaleString('en-US');
}

/**
 * Format a fraction in `[0, 1]` as a percentage with one decimal.
 *
 * @param fraction - The fraction to format.
 * @returns A percentage string (e.g. `4.2%`), or an em dash if not finite.
 */
function formatPct(fraction: number): string {
  if (!Number.isFinite(fraction)) {
    return '—';
  }
  return `${(fraction * 100).toFixed(1)}%`;
}

/**
 * Format a floating-point value with compact, readable precision.
 *
 * Falls back to scientific notation for very large/small magnitudes (useful for
 * adjusted p-values that can be astronomically small).
 *
 * @param v - The value to format.
 * @returns A short numeric string, or an em dash for non-finite input.
 */
function formatNum(v: number): string {
  if (!Number.isFinite(v)) {
    return '—';
  }
  if (v === 0) {
    return '0';
  }
  const abs = Math.abs(v);
  if (abs !== 0 && (abs >= 1e4 || abs < 1e-3)) {
    return v.toExponential(1);
  }
  return v.toFixed(2);
}

/**
 * SelectionPanel. Reads selection state and stats from the store and lets the
 * user (re)compute or clear them.
 *
 * @returns The selection panel element.
 */
export function SelectionPanel(): JSX.Element {
  const dataset = useStore((s) => s.dataset);
  const selection = useStore((s) => s.selection);
  const stats = useStore((s) => s.selectionStats);
  const fetchSelectionStats = useStore((s) => s.fetchSelectionStats);
  const clearSelection = useStore((s) => s.clearSelection);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const nSelected = selection?.length ?? 0;
  const hasSelection = nSelected > 0;

  const onFetch = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      await fetchSelectionStats();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full flex-col gap-3 text-sm text-slate-200">
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-100">Selection</h2>
        <button
          type="button"
          onClick={() => clearSelection()}
          disabled={!hasSelection}
          className="rounded border border-slate-600 px-2 py-0.5 text-xs text-slate-300 transition-colors hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Clear
        </button>
      </header>

      {!hasSelection ? (
        <p className="text-xs text-slate-500">
          No cells selected. Use the box or lasso tool to select a subset.
        </p>
      ) : (
        <>
          <div className="flex items-baseline gap-2">
            <span className="text-lg font-semibold text-sky-400">
              {formatInt(nSelected)}
            </span>
            <span className="text-xs text-slate-400">
              cells selected
              {dataset
                ? ` · ${formatPct(nSelected / dataset.n_obs)} of dataset`
                : ''}
            </span>
          </div>

          <button
            type="button"
            onClick={() => void onFetch()}
            disabled={busy}
            className="self-start rounded bg-sky-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy
              ? 'Computing…'
              : stats
                ? 'Refresh stats'
                : 'Compute stats & markers'}
          </button>

          {error && (
            <p className="rounded border border-rose-700 bg-rose-950/50 px-2 py-1 text-xs text-rose-300">
              {error}
            </p>
          )}

          {!stats && !busy && (
            <p className="text-xs text-slate-500">
              Marker genes and obs summaries are computed on demand — they can be
              expensive on large selections.
            </p>
          )}

          {stats && <StatsBody stats={stats} />}
        </>
      )}
    </div>
  );
}

/** Props for {@link StatsBody}. */
interface StatsBodyProps {
  /** The computed selection statistics. */
  stats: SelectionStatsResponse;
}

/**
 * Render the body of computed selection stats: the markers table, per-column
 * obs summaries, and any honest caveat notes from the server.
 *
 * @param props - See {@link StatsBodyProps}.
 * @returns The stats body element.
 */
function StatsBody({ stats }: StatsBodyProps): JSX.Element {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto pr-1">
      <p className="text-[11px] text-slate-400">
        Fraction {formatPct(stats.fraction)} · markers vs{' '}
        {formatInt(stats.rest_cells_used)} rest cells
      </p>

      <MarkersTable markers={stats.markers} />

      <ObsSummaries summary={stats.obs_summary} />

      {stats.notes.length > 0 && (
        <section className="flex flex-col gap-1">
          <span className="text-xs font-medium text-slate-400">Notes</span>
          <ul className="flex flex-col gap-0.5">
            {stats.notes.map((note, idx) => (
              <li
                key={`${idx}:${note}`}
                className="text-[11px] leading-snug text-amber-300/80"
              >
                • {note}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/** Props for {@link MarkersTable}. */
interface MarkersTableProps {
  /** Ranked marker genes for the selection. */
  markers: MarkerGene[];
}

/**
 * Render the top marker genes as a compact table.
 *
 * Columns: gene name, log2 fold-change, adjusted p-value, and the fraction of
 * selected vs rest cells expressing the gene.
 *
 * @param props - See {@link MarkersTableProps}.
 * @returns The markers table element.
 */
function MarkersTable({ markers }: MarkersTableProps): JSX.Element {
  return (
    <section className="flex flex-col gap-1">
      <span className="text-xs font-medium text-slate-400">
        Top markers ({markers.length})
      </span>
      {markers.length === 0 ? (
        <p className="text-xs text-slate-500">No markers computed.</p>
      ) : (
        <div className="overflow-x-auto rounded border border-slate-700">
          <table className="w-full border-collapse text-[11px]">
            <thead>
              <tr className="bg-slate-800 text-left text-slate-400">
                <th className="px-1.5 py-1 font-medium">Gene</th>
                <th className="px-1.5 py-1 text-right font-medium">log2FC</th>
                <th className="px-1.5 py-1 text-right font-medium">padj</th>
                <th className="px-1.5 py-1 text-right font-medium">in</th>
                <th className="px-1.5 py-1 text-right font-medium">out</th>
              </tr>
            </thead>
            <tbody>
              {markers.map((m) => (
                <tr
                  key={m.name}
                  className="border-t border-slate-800 odd:bg-slate-900/40"
                >
                  <td
                    className="max-w-[8rem] truncate px-1.5 py-1 font-mono text-slate-100"
                    title={m.name}
                  >
                    {m.name}
                  </td>
                  <td
                    className={
                      'px-1.5 py-1 text-right font-mono ' +
                      (m.log2fc >= 0 ? 'text-emerald-400' : 'text-rose-400')
                    }
                  >
                    {formatNum(m.log2fc)}
                  </td>
                  <td className="px-1.5 py-1 text-right font-mono text-slate-300">
                    {formatNum(m.pval_adj)}
                  </td>
                  <td className="px-1.5 py-1 text-right font-mono text-slate-400">
                    {formatPct(m.pct_in)}
                  </td>
                  <td className="px-1.5 py-1 text-right font-mono text-slate-500">
                    {formatPct(m.pct_out)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** Props for {@link ObsSummaries}. */
interface ObsSummariesProps {
  /** Per-obs-column summaries keyed by column name. */
  summary: Record<string, ObsColumnSummary>;
}

/**
 * Render the obs-column summaries: categorical category counts, or continuous
 * descriptive statistics, for each summarized column.
 *
 * @param props - See {@link ObsSummariesProps}.
 * @returns The obs-summary element.
 */
function ObsSummaries({ summary }: ObsSummariesProps): JSX.Element {
  const entries = Object.entries(summary);

  return (
    <section className="flex flex-col gap-2">
      <span className="text-xs font-medium text-slate-400">
        Metadata summary
      </span>
      {entries.length === 0 ? (
        <p className="text-xs text-slate-500">No obs columns summarized.</p>
      ) : (
        entries.map(([name, col]) => (
          <div
            key={name}
            className="rounded border border-slate-700 bg-slate-800/40 p-2"
          >
            <div className="mb-1 flex items-center justify-between">
              <span className="truncate font-medium text-slate-200" title={name}>
                {name}
              </span>
              <span className="text-[10px] uppercase tracking-wide text-slate-500">
                {col.kind}
              </span>
            </div>
            {col.kind === 'categorical' ? (
              <CategoricalSummaryView summary={col} />
            ) : (
              <ContinuousSummaryView summary={col} />
            )}
          </div>
        ))
      )}
    </section>
  );
}

/** Props for {@link CategoricalSummaryView}. */
interface CategoricalSummaryViewProps {
  /** A categorical obs-column summary. */
  summary: Extract<ObsColumnSummary, { kind: 'categorical' }>;
}

/**
 * Render category counts (sorted descending), highlighting the top label.
 *
 * @param props - See {@link CategoricalSummaryViewProps}.
 * @returns The categorical summary element.
 */
function CategoricalSummaryView({
  summary,
}: CategoricalSummaryViewProps): JSX.Element {
  const rows = Object.entries(summary.counts).sort((a, b) => b[1] - a[1]);
  const total = rows.reduce((acc, [, count]) => acc + count, 0);

  return (
    <ul className="flex flex-col gap-0.5">
      {rows.map(([label, count]) => {
        const isTop = label === summary.top;
        const pct = total > 0 ? (count / total) * 100 : 0;
        return (
          <li
            key={label}
            className="flex items-center justify-between gap-2 text-[11px]"
          >
            <span
              className={
                'truncate ' + (isTop ? 'text-sky-300' : 'text-slate-300')
              }
              title={label}
            >
              {label}
            </span>
            <span className="shrink-0 font-mono text-slate-400">
              {formatInt(count)} ({pct.toFixed(0)}%)
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** Props for {@link ContinuousSummaryView}. */
interface ContinuousSummaryViewProps {
  /** A continuous obs-column summary. */
  summary: Extract<ObsColumnSummary, { kind: 'continuous' }>;
}

/**
 * Render the descriptive statistics (mean, median, min, max, std) of a
 * continuous obs column over the selection.
 *
 * @param props - See {@link ContinuousSummaryViewProps}.
 * @returns The continuous summary element.
 */
function ContinuousSummaryView({
  summary,
}: ContinuousSummaryViewProps): JSX.Element {
  const items: ReadonlyArray<readonly [string, number]> = [
    ['mean', summary.mean],
    ['median', summary.median],
    ['min', summary.min],
    ['max', summary.max],
    ['std', summary.std],
  ];

  return (
    <dl className="grid grid-cols-3 gap-x-2 gap-y-0.5 text-[11px]">
      {items.map(([label, value]) => (
        <div key={label} className="flex flex-col">
          <dt className="text-slate-500">{label}</dt>
          <dd className="font-mono text-slate-200">{formatNum(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

export default SelectionPanel;
