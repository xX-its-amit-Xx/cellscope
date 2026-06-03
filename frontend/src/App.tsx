// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * App — top-level CellScope layout.
 *
 * Composes the application chrome around the embedding viewport:
 *  - a top {@link Toolbar} (title, dataset info, embedding/colormap/tool/
 *    downsample controls);
 *  - a left sidebar with the {@link FileLoader}, {@link ColorByPanel}, and
 *    {@link RecomputePanel};
 *  - the {@link EmbeddingViewport} filling the center;
 *  - a right {@link SelectionPanel}, shown only while a selection exists.
 *
 * When no dataset is loaded the viewport area is replaced by a centered
 * file-loading prompt. Store-level `error` renders as a dismissible banner and
 * `loading` shows a global spinner overlay. All state comes from the Zustand
 * store (`useStore`); this component holds no business logic of its own.
 */

import { useStore } from './store/useStore';
import { Toolbar } from './components/Toolbar';
import { FileLoader } from './components/FileLoader';
import { ColorByPanel } from './components/ColorByPanel';
import { RecomputePanel } from './components/RecomputePanel';
import { SelectionPanel } from './components/SelectionPanel';
import { EmbeddingViewport } from './components/EmbeddingViewport';

/**
 * Root application component wiring the layout to the global store.
 *
 * @returns The full application shell.
 */
export function App(): JSX.Element {
  const dataset = useStore((s) => s.dataset);
  const loading = useStore((s) => s.loading);
  const error = useStore((s) => s.error);
  const clearError = useStore((s) => s.clearError);
  const selection = useStore((s) => s.selection);

  const hasDataset = dataset !== null;
  const hasSelection = (selection?.length ?? 0) > 0;

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-slate-950 text-slate-100">
      <Toolbar />

      {error && (
        <ErrorBanner
          message={error}
          onDismiss={() => clearError()}
        />
      )}

      <div className="relative flex min-h-0 flex-1">
        {/* Left sidebar: loading + coloring + recompute controls. */}
        <aside className="flex w-72 shrink-0 flex-col gap-4 overflow-y-auto border-r border-slate-800 bg-slate-900 p-3">
          <Section title="Dataset">
            <FileLoader />
          </Section>
          {hasDataset && (
            <>
              <Section title="Color by">
                <ColorByPanel />
              </Section>
              <Section title="Recompute">
                <RecomputePanel />
              </Section>
            </>
          )}
        </aside>

        {/* Center: the embedding viewport, or a centered loader prompt. */}
        <main className="relative min-w-0 flex-1 bg-slate-950">
          {hasDataset ? (
            <EmbeddingViewport />
          ) : (
            <EmptyState loading={loading} />
          )}
          {loading && hasDataset && <LoadingOverlay />}
        </main>

        {/* Right sidebar: selection stats, only when something is selected. */}
        {hasDataset && hasSelection && (
          <aside className="flex w-80 shrink-0 flex-col overflow-hidden border-l border-slate-800 bg-slate-900 p-3">
            <SelectionPanel />
          </aside>
        )}
      </div>
    </div>
  );
}

/** Props for {@link Section}. */
interface SectionProps {
  /** Section heading text. */
  title: string;
  /** Section body. */
  children: React.ReactNode;
}

/**
 * A titled sidebar section with a small uppercase heading.
 *
 * @param props - See {@link SectionProps}.
 * @returns The section element.
 */
function Section({ title, children }: SectionProps): JSX.Element {
  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </h2>
      {children}
    </section>
  );
}

/** Props for {@link ErrorBanner}. */
interface ErrorBannerProps {
  /** The error message to display. */
  message: string;
  /** Invoked when the user dismisses the banner. */
  onDismiss: () => void;
}

/**
 * A dismissible error banner spanning the width below the toolbar.
 *
 * @param props - See {@link ErrorBannerProps}.
 * @returns The banner element.
 */
function ErrorBanner({ message, onDismiss }: ErrorBannerProps): JSX.Element {
  return (
    <div
      role="alert"
      className="flex shrink-0 items-start gap-3 border-b border-rose-800 bg-rose-950/70 px-4 py-2 text-sm text-rose-200"
    >
      <span className="mt-0.5 shrink-0" aria-hidden="true">
        ⚠
      </span>
      <span className="min-w-0 flex-1 break-words">{message}</span>
      <button
        type="button"
        onClick={onDismiss}
        className="shrink-0 rounded px-2 py-0.5 text-xs text-rose-300 transition-colors hover:bg-rose-900/60"
        aria-label="Dismiss error"
      >
        Dismiss
      </button>
    </div>
  );
}

/** Props for {@link EmptyState}. */
interface EmptyStateProps {
  /** Whether a load is currently in flight. */
  loading: boolean;
}

/**
 * Centered prompt shown in the viewport area before any dataset is loaded.
 * Embeds a {@link FileLoader} so the user can load a dataset without first
 * reaching for the sidebar.
 *
 * @param props - See {@link EmptyStateProps}.
 * @returns The empty-state element.
 */
function EmptyState({ loading }: EmptyStateProps): JSX.Element {
  return (
    <div className="flex h-full w-full items-center justify-center p-6">
      <div className="w-full max-w-md rounded-xl border border-slate-800 bg-slate-900/80 p-6 shadow-xl">
        <h1 className="mb-1 text-center text-xl font-semibold text-slate-100">
          Load a dataset
        </h1>
        <p className="mb-4 text-center text-sm text-slate-400">
          Drop an <code className="text-sky-400">.h5ad</code> file, load one by
          server-side path, or pick from the datasets discovered on the server.
        </p>
        <FileLoader />
        {loading && (
          <p className="mt-3 flex items-center justify-center gap-2 text-xs text-slate-400">
            <Spinner />
            Loading dataset…
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * A semi-transparent overlay with a spinner, shown over the viewport while a
 * background load is in flight after a dataset is already present.
 *
 * @returns The overlay element.
 */
function LoadingOverlay(): JSX.Element {
  return (
    <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-slate-950/40">
      <div className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/90 px-4 py-2 text-sm text-slate-200 shadow-lg">
        <Spinner />
        Working…
      </div>
    </div>
  );
}

/**
 * A small animated loading spinner (pure CSS via Tailwind's `animate-spin`).
 *
 * @returns The spinner element.
 */
function Spinner(): JSX.Element {
  return (
    <span
      className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-slate-600 border-t-sky-400"
      role="status"
      aria-label="Loading"
    />
  );
}

export default App;
