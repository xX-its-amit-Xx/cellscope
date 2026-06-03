// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * FileLoader — load an AnnData (`.h5ad`) dataset.
 *
 * Offers three entry points:
 *  1. A drag-and-drop zone (also click-to-pick) that uploads a local `.h5ad`
 *     via `store.uploadFile`.
 *  2. A text input for a server-side path, submitted via `store.loadByPath`.
 *  3. A list of datasets discovered on the server (`client.listDatasets`),
 *     each click-to-load by path.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useStore } from '../store/useStore';
import * as client from '../api/client';
import type { DatasetListResponse } from '../api/client';
import type { DatasetSummary } from '../types';

/**
 * Derive a short, human-friendly display name for a loaded dataset.
 *
 * `DatasetSummary` carries no explicit name field (CONTRACT does not define
 * one), so prefer the file basename of `path` and fall back to a truncated id.
 *
 * @param summary - The loaded-dataset summary.
 * @returns A display name.
 */
function summaryName(summary: DatasetSummary): string {
  if (summary.path) {
    const parts = summary.path.split(/[\\/]/);
    const base = parts[parts.length - 1];
    if (base) {
      return base;
    }
  }
  return summary.dataset_id.slice(0, 8);
}

/** True when a file looks like an `.h5ad` by extension. */
function isH5ad(name: string): boolean {
  return name.toLowerCase().endsWith('.h5ad');
}

/**
 * FileLoader panel. Self-contained: fetches the server dataset listing on
 * mount and drives all loading through store actions.
 *
 * @returns The file loader element.
 */
export function FileLoader(): JSX.Element {
  const loading = useStore((s) => s.loading);
  const uploadFile = useStore((s) => s.uploadFile);
  const loadByPath = useStore((s) => s.loadByPath);

  const [pathInput, setPathInput] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const [listing, setListing] = useState<DatasetListResponse | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const refreshListing = useCallback(async (): Promise<void> => {
    setListError(null);
    try {
      const result = await client.listDatasets();
      setListing(result);
    } catch (err) {
      setListError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refreshListing();
  }, [refreshListing]);

  const handleFiles = useCallback(
    (files: FileList | null): void => {
      setLocalError(null);
      const file = files?.[0];
      if (!file) {
        return;
      }
      if (!isH5ad(file.name)) {
        setLocalError(`Not an .h5ad file: ${file.name}`);
        return;
      }
      void uploadFile(file);
    },
    [uploadFile],
  );

  const onDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>): void => {
      e.preventDefault();
      setDragOver(false);
      handleFiles(e.dataTransfer.files);
    },
    [handleFiles],
  );

  const onPathSubmit = useCallback(
    (e: React.FormEvent<HTMLFormElement>): void => {
      e.preventDefault();
      setLocalError(null);
      const trimmed = pathInput.trim();
      if (!trimmed) {
        return;
      }
      void loadByPath(trimmed);
    },
    [loadByPath, pathInput],
  );

  return (
    <div className="flex flex-col gap-4 text-sm text-slate-200">
      <div
        className={
          'flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-center transition-colors ' +
          (dragOver
            ? 'border-sky-500 bg-sky-950/40'
            : 'border-slate-600 bg-slate-800/40 hover:border-slate-500')
        }
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => fileInputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            fileInputRef.current?.click();
          }
        }}
        aria-label="Drag and drop an .h5ad file or click to browse"
      >
        <span className="text-2xl" aria-hidden="true">
          ⬆
        </span>
        <span className="font-medium text-slate-100">
          Drop an <code className="text-sky-400">.h5ad</code> file here
        </span>
        <span className="text-xs text-slate-400">or click to browse</span>
        <input
          ref={fileInputRef}
          type="file"
          accept=".h5ad"
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </div>

      <form onSubmit={onPathSubmit} className="flex flex-col gap-1.5">
        <label
          htmlFor="cellscope-path-input"
          className="text-xs font-medium text-slate-400"
        >
          Or load a server-side path
        </label>
        <div className="flex gap-2">
          <input
            id="cellscope-path-input"
            type="text"
            value={pathInput}
            onChange={(e) => setPathInput(e.target.value)}
            placeholder="/data/pbmc3k.h5ad"
            className="min-w-0 flex-1 rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-slate-100 placeholder:text-slate-500 focus:border-sky-500 focus:outline-none"
            spellCheck={false}
          />
          <button
            type="submit"
            disabled={loading || pathInput.trim() === ''}
            className="rounded bg-sky-600 px-3 py-1.5 font-medium text-white transition-colors hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Load
          </button>
        </div>
      </form>

      {localError && (
        <p className="rounded border border-rose-700 bg-rose-950/50 px-2 py-1 text-xs text-rose-300">
          {localError}
        </p>
      )}

      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-slate-400">
            Available datasets
          </span>
          <button
            type="button"
            onClick={() => void refreshListing()}
            className="text-xs text-sky-400 hover:text-sky-300"
          >
            Refresh
          </button>
        </div>

        {listError && (
          <p className="text-xs text-rose-400">Failed to list: {listError}</p>
        )}

        {listing && (
          <DatasetListing
            listing={listing}
            loading={loading}
            onLoadPath={(p) => void loadByPath(p)}
          />
        )}

        {loading && (
          <p className="flex items-center gap-2 text-xs text-slate-400">
            <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-slate-600 border-t-sky-400" />
            Loading…
          </p>
        )}
      </div>
    </div>
  );
}

/** Props for {@link DatasetListing}. */
interface DatasetListingProps {
  /** The server response listing loaded + discoverable datasets. */
  listing: DatasetListResponse;
  /** Whether a load is currently in flight (disables list buttons). */
  loading: boolean;
  /** Invoked with a server-side path to load. */
  onLoadPath: (path: string) => void;
}

/**
 * Render the loaded datasets and the discoverable `.h5ad` files.
 *
 * @param props - See {@link DatasetListingProps}.
 * @returns The listing element.
 */
function DatasetListing({
  listing,
  loading,
  onLoadPath,
}: DatasetListingProps): JSX.Element {
  const hasLoaded = listing.loaded.length > 0;
  const hasAvailable = listing.available_files.length > 0;

  if (!hasLoaded && !hasAvailable) {
    return (
      <p className="text-xs text-slate-500">No datasets found on the server.</p>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {hasLoaded && (
        <ul className="flex flex-col gap-1">
          {listing.loaded.map((d: DatasetSummary) => (
            <li key={d.dataset_id}>
              <button
                type="button"
                disabled={loading || d.path === null}
                onClick={() => {
                  if (d.path) {
                    onLoadPath(d.path);
                  }
                }}
                className="flex w-full items-center justify-between rounded border border-slate-700 bg-slate-800/60 px-2 py-1.5 text-left text-xs transition-colors hover:border-sky-600 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                title={d.path ?? d.dataset_id}
              >
                <span className="truncate text-slate-100">
                  {summaryName(d)}
                </span>
                <span className="shrink-0 text-slate-400">
                  {d.n_obs.toLocaleString('en-US')} cells
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {hasAvailable && (
        <ul className="flex flex-col gap-1">
          {listing.available_files.map((file) => (
            <li key={file}>
              <button
                type="button"
                disabled={loading}
                onClick={() => onLoadPath(file)}
                className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs text-slate-300 transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                title={file}
              >
                <span className="text-slate-500" aria-hidden="true">
                  📄
                </span>
                <span className="truncate">{file}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default FileLoader;
