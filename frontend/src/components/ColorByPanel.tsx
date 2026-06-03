// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * ColorByPanel — choose what colors the scatter.
 *
 * Provides a debounced gene search (results clickable -> `colorByGene`), a
 * list of obs columns (clickable -> `colorByObs`), a clear-color button, and
 * a small legend reflecting the active coloring: a continuous colormap
 * gradient with its numeric domain, or categorical category swatches.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useStore } from '../store/useStore';
import * as client from '../api/client';
import { categoryColor, continuousColor } from '../lib/colormap';
import type { GeneHit, ObsColumnInfo } from '../types';

/** Debounce delay (ms) for gene search requests. */
const SEARCH_DEBOUNCE_MS = 200;
/** Max gene hits requested per query. */
const SEARCH_LIMIT = 50;
/** Number of gradient stops sampled for the continuous legend. */
const GRADIENT_STOPS = 24;
/** Max categorical swatches rendered before collapsing to a count. */
const MAX_SWATCHES = 50;

/**
 * Convert an `[r,g,b]` triple (0-255) to a CSS `rgb()` string.
 *
 * @param rgb - The color triple.
 * @returns A CSS color string.
 */
function rgbCss(rgb: readonly [number, number, number]): string {
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}

/**
 * Format a number for the legend domain labels with sensible precision.
 *
 * @param v - The value to format.
 * @returns A short numeric string.
 */
function fmt(v: number): string {
  if (!Number.isFinite(v)) {
    return '—';
  }
  if (v === 0) {
    return '0';
  }
  const abs = Math.abs(v);
  if (abs >= 1000 || abs < 0.01) {
    return v.toExponential(1);
  }
  return v.toFixed(2);
}

/**
 * ColorByPanel. Reads the dataset's obs columns and active color state from
 * the store; performs its own debounced gene search against the REST client.
 *
 * @returns The color-by panel element.
 */
export function ColorByPanel(): JSX.Element {
  const dataset = useStore((s) => s.dataset);
  const colorMode = useStore((s) => s.colorMode);
  const colorKind = useStore((s) => s.colorKind);
  const colorDomain = useStore((s) => s.colorDomain);
  const categories = useStore((s) => s.categories);
  const colormapName = useStore((s) => s.colormapName);
  const colorByGene = useStore((s) => s.colorByGene);
  const colorByObs = useStore((s) => s.colorByObs);
  const clearColor = useStore((s) => s.clearColor);

  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<GeneHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const datasetId = dataset?.dataset_id ?? null;

  const runSearch = useCallback(
    async (q: string): Promise<void> => {
      if (!datasetId) {
        return;
      }
      setSearching(true);
      setSearchError(null);
      try {
        const result = await client.searchGenes(datasetId, q, SEARCH_LIMIT);
        setHits(result.hits);
      } catch (err) {
        setSearchError(err instanceof Error ? err.message : String(err));
        setHits([]);
      } finally {
        setSearching(false);
      }
    },
    [datasetId],
  );

  useEffect(() => {
    if (!datasetId) {
      return;
    }
    if (timerRef.current) {
      clearTimeout(timerRef.current);
    }
    timerRef.current = setTimeout(() => {
      void runSearch(query);
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    };
  }, [query, datasetId, runSearch]);

  const activeGene = colorMode.type === 'gene' ? colorMode.gene : null;
  const activeObs = colorMode.type === 'obs' ? colorMode.column : null;

  const obsColumns: ObsColumnInfo[] = dataset?.obs_columns ?? [];

  if (!dataset) {
    return (
      <p className="text-xs text-slate-500">Load a dataset to color cells.</p>
    );
  }

  return (
    <div className="flex flex-col gap-3 text-sm text-slate-200">
      <section className="flex flex-col gap-1.5">
        <label
          htmlFor="cellscope-gene-search"
          className="text-xs font-medium text-slate-400"
        >
          Color by gene
        </label>
        <input
          id="cellscope-gene-search"
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search genes (e.g. CD3D)…"
          className="rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-slate-100 placeholder:text-slate-500 focus:border-sky-500 focus:outline-none"
          spellCheck={false}
          autoComplete="off"
        />
        {searchError && (
          <p className="text-xs text-rose-400">{searchError}</p>
        )}
        {hits.length > 0 && (
          <ul className="max-h-40 overflow-y-auto rounded border border-slate-700 bg-slate-800/60">
            {hits.map((hit) => {
              const active = activeGene === hit.name;
              return (
                <li key={`${hit.name}:${hit.index}`}>
                  <button
                    type="button"
                    onClick={() => colorByGene(hit.name)}
                    className={
                      'flex w-full items-center justify-between px-2 py-1 text-left text-xs transition-colors ' +
                      (active
                        ? 'bg-sky-700 text-white'
                        : 'text-slate-200 hover:bg-slate-700')
                    }
                  >
                    <span className="truncate font-mono">{hit.name}</span>
                    <span className="shrink-0 text-slate-500">
                      #{hit.index}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        {searching && (
          <p className="text-xs text-slate-500">Searching…</p>
        )}
        {!searching && query.trim() !== '' && hits.length === 0 && !searchError && (
          <p className="text-xs text-slate-500">No matching genes.</p>
        )}
      </section>

      <section className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-slate-400">
          Color by metadata
        </span>
        {obsColumns.length === 0 ? (
          <p className="text-xs text-slate-500">No obs columns.</p>
        ) : (
          <ul className="flex max-h-48 flex-col gap-0.5 overflow-y-auto">
            {obsColumns.map((col) => {
              const active = activeObs === col.name;
              return (
                <li key={col.name}>
                  <button
                    type="button"
                    onClick={() => colorByObs(col.name)}
                    className={
                      'flex w-full items-center justify-between rounded px-2 py-1 text-left text-xs transition-colors ' +
                      (active
                        ? 'bg-sky-700 text-white'
                        : 'text-slate-200 hover:bg-slate-700')
                    }
                  >
                    <span className="truncate">{col.name}</span>
                    <span className="shrink-0 text-[10px] uppercase tracking-wide text-slate-500">
                      {col.kind === 'categorical'
                        ? `${col.n_categories ?? '?'} cats`
                        : 'cont'}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <button
        type="button"
        onClick={() => clearColor()}
        disabled={colorMode.type === 'none'}
        className="self-start rounded border border-slate-600 px-2.5 py-1 text-xs text-slate-300 transition-colors hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
      >
        Clear color
      </button>

      <Legend
        colorKind={colorKind}
        colorDomain={colorDomain}
        categories={categories}
        colormapName={colormapName}
      />
    </div>
  );
}

/** Props for {@link Legend}. */
interface LegendProps {
  /** The active coloring kind. */
  colorKind: 'none' | 'continuous' | 'categorical';
  /** `[min, max]` for continuous coloring, else null. */
  colorDomain: readonly [number, number] | null;
  /** Ordered category labels for categorical coloring, else null. */
  categories: readonly string[] | null;
  /** The active continuous colormap name. */
  colormapName: string;
}

/**
 * Render a legend matching the active coloring: a continuous gradient bar
 * with its domain, or categorical swatches.
 *
 * @param props - See {@link LegendProps}.
 * @returns The legend element, or null when nothing is colored.
 */
function Legend({
  colorKind,
  colorDomain,
  categories,
  colormapName,
}: LegendProps): JSX.Element | null {
  const gradient = useMemo(() => {
    if (colorKind !== 'continuous') {
      return '';
    }
    const stops: string[] = [];
    for (let i = 0; i < GRADIENT_STOPS; i += 1) {
      const t = i / (GRADIENT_STOPS - 1);
      const rgb = continuousColor(colormapName, t);
      const pct = Math.round(t * 100);
      stops.push(`${rgbCss(rgb)} ${pct}%`);
    }
    return `linear-gradient(to right, ${stops.join(', ')})`;
  }, [colorKind, colormapName]);

  if (colorKind === 'continuous' && colorDomain) {
    return (
      <section className="flex flex-col gap-1">
        <span className="text-xs font-medium text-slate-400">Legend</span>
        <div
          className="h-3 w-full rounded"
          style={{ background: gradient }}
          aria-hidden="true"
        />
        <div className="flex justify-between text-[10px] text-slate-400">
          <span>{fmt(colorDomain[0])}</span>
          <span>{fmt(colorDomain[1])}</span>
        </div>
      </section>
    );
  }

  if (colorKind === 'categorical' && categories && categories.length > 0) {
    const shown = categories.slice(0, MAX_SWATCHES);
    const overflow = categories.length - shown.length;
    return (
      <section className="flex flex-col gap-1">
        <span className="text-xs font-medium text-slate-400">Legend</span>
        <ul className="flex max-h-40 flex-col gap-0.5 overflow-y-auto">
          {shown.map((label, idx) => (
            <li
              key={`${label}:${idx}`}
              className="flex items-center gap-1.5 text-xs text-slate-300"
            >
              <span
                className="inline-block h-3 w-3 shrink-0 rounded-sm border border-slate-600"
                style={{ backgroundColor: rgbCss(categoryColor(idx)) }}
                aria-hidden="true"
              />
              <span className="truncate">{label}</span>
            </li>
          ))}
        </ul>
        {overflow > 0 && (
          <span className="text-[10px] text-slate-500">
            +{overflow} more categories
          </span>
        )}
      </section>
    );
  }

  return null;
}

export default ColorByPanel;
