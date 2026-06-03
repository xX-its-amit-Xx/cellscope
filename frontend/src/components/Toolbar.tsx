// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * Toolbar — the top application bar.
 *
 * Shows the app title, the loaded dataset name + cell count, an embedding
 * selector, the selection tool toggles (pan/box/lasso), a downsampling
 * toggle, and a colormap dropdown.
 *
 * Cross-file coordination note: the active selection tool lives on the
 * Zustand store as `activeTool` (with the `setActiveTool` action) so that
 * `EmbeddingViewport` can read it directly without prop drilling. The store
 * owns both the field and its `SelectionTool` type (exported from `../types`).
 */

import { useStore } from '../store/useStore';
import { listColormaps } from '../lib/colormap';
import type { SelectionTool } from '../types';

/** Human label for a selection tool. */
const TOOL_LABELS: Record<SelectionTool, string> = {
  pan: 'Pan',
  box: 'Box',
  lasso: 'Lasso',
};

/** Compact glyph for each selection tool (kept as text to avoid icon deps). */
const TOOL_GLYPHS: Record<SelectionTool, string> = {
  pan: '✋',
  box: '▢',
  lasso: '◌',
};

const TOOL_ORDER: readonly SelectionTool[] = ['pan', 'box', 'lasso'] as const;

/**
 * Format a large integer with thousands separators for display.
 *
 * @param n - The integer to format.
 * @returns A locale-grouped string (e.g. `1,234,567`).
 */
function formatCount(n: number): string {
  return n.toLocaleString('en-US');
}

/**
 * Derive a short, human-friendly dataset name from a `DatasetInfo`.
 *
 * Prefers the file basename of `path`; falls back to a truncated id.
 *
 * @param path - The dataset path, if any.
 * @param datasetId - The dataset id (uuid hex).
 * @returns A display name.
 */
function datasetDisplayName(path: string | null, datasetId: string): string {
  if (path) {
    const parts = path.split(/[\\/]/);
    const base = parts[parts.length - 1];
    if (base) {
      return base;
    }
  }
  return datasetId.slice(0, 8);
}

/**
 * The top application bar. Renders nothing meaningful until a dataset is
 * loaded, but the title is always shown so the chrome is stable.
 *
 * @returns The toolbar element.
 */
export function Toolbar(): JSX.Element {
  const dataset = useStore((s) => s.dataset);
  const embeddingKey = useStore((s) => s.embeddingKey);
  const setEmbedding = useStore((s) => s.setEmbedding);
  const activeTool = useStore((s) => s.activeTool);
  const setActiveTool = useStore((s) => s.setActiveTool);
  const downsample = useStore((s) => s.downsample);
  const setDownsample = useStore((s) => s.setDownsample);
  const colormapName = useStore((s) => s.colormapName);
  const setColormap = useStore((s) => s.setColormap);

  const colormaps = listColormaps();

  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-slate-700 bg-slate-900 px-4 text-sm text-slate-200">
      <div className="flex items-center gap-2">
        <span className="text-base font-semibold tracking-tight text-sky-400">
          CellScope
        </span>
        <span className="hidden text-xs text-slate-500 sm:inline">
          single-cell browser
        </span>
      </div>

      {dataset && (
        <>
          <div className="h-5 w-px bg-slate-700" aria-hidden="true" />

          <div className="flex min-w-0 items-center gap-2">
            <span
              className="truncate font-medium text-slate-100"
              title={dataset.path ?? dataset.dataset_id}
            >
              {datasetDisplayName(dataset.path, dataset.dataset_id)}
            </span>
            <span className="shrink-0 text-xs text-slate-400">
              {formatCount(dataset.n_obs)} cells · {formatCount(dataset.n_vars)}{' '}
              genes
            </span>
          </div>

          {dataset.embeddings.length > 0 && (
            <label className="flex items-center gap-1.5 text-xs text-slate-400">
              <span className="hidden md:inline">Embedding</span>
              <select
                className="rounded border border-slate-600 bg-slate-800 px-2 py-1 text-xs text-slate-100 focus:border-sky-500 focus:outline-none"
                value={embeddingKey ?? dataset.default_embedding ?? ''}
                onChange={(e) => setEmbedding(e.target.value)}
                aria-label="Select embedding"
              >
                {dataset.embeddings.map((key) => (
                  <option key={key} value={key}>
                    {key}
                  </option>
                ))}
              </select>
            </label>
          )}

          <div className="ml-auto flex items-center gap-3">
            <div
              className="flex items-center gap-0.5 rounded border border-slate-700 bg-slate-800 p-0.5"
              role="group"
              aria-label="Selection tool"
            >
              {TOOL_ORDER.map((tool) => {
                const active = activeTool === tool;
                return (
                  <button
                    key={tool}
                    type="button"
                    onClick={() => setActiveTool(tool)}
                    title={`${TOOL_LABELS[tool]} tool`}
                    aria-pressed={active}
                    className={
                      'flex items-center gap-1 rounded px-2 py-1 text-xs transition-colors ' +
                      (active
                        ? 'bg-sky-600 text-white'
                        : 'text-slate-300 hover:bg-slate-700')
                    }
                  >
                    <span aria-hidden="true">{TOOL_GLYPHS[tool]}</span>
                    <span className="hidden lg:inline">
                      {TOOL_LABELS[tool]}
                    </span>
                  </button>
                );
              })}
            </div>

            <label className="flex cursor-pointer items-center gap-1.5 text-xs text-slate-300">
              <input
                type="checkbox"
                className="h-3.5 w-3.5 rounded border-slate-600 bg-slate-800 text-sky-600 focus:ring-sky-500"
                checked={downsample}
                onChange={(e) => setDownsample(e.target.checked)}
              />
              <span>Downsample</span>
            </label>

            <label className="flex items-center gap-1.5 text-xs text-slate-400">
              <span className="hidden md:inline">Colormap</span>
              <select
                className="rounded border border-slate-600 bg-slate-800 px-2 py-1 text-xs text-slate-100 focus:border-sky-500 focus:outline-none"
                value={colormapName}
                onChange={(e) => setColormap(e.target.value)}
                aria-label="Select colormap"
              >
                {colormaps.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </>
      )}
    </header>
  );
}

export default Toolbar;
