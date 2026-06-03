// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * Tooltip — a small floating panel shown next to the cursor while hovering a
 * cell in the embedding viewport.
 *
 * It is positioned in screen (CSS-pixel) space and never intercepts pointer
 * events (`pointer-events: none`) so it cannot interfere with deck.gl picking
 * or the selection overlay. Content is supplied by the parent as a hovered cell
 * index plus a few key/value info lines (e.g. the active color value, an obs
 * label).
 */

import { useMemo, type CSSProperties } from 'react';

/**
 * A single label/value pair rendered as one row in the tooltip body.
 */
export interface TooltipLine {
  /** Short field label (e.g. `"value"`, `"leiden"`). */
  label: string;
  /** Rendered value (already formatted to a string). */
  value: string;
}

/**
 * Props for {@link Tooltip}.
 */
export interface TooltipProps {
  /** Screen-space X position (CSS pixels, relative to the viewport container). */
  x: number;
  /** Screen-space Y position (CSS pixels, relative to the viewport container). */
  y: number;
  /** Zero-based index of the hovered cell in the FULL dataset. */
  index: number;
  /** Extra info lines (e.g. the active color value). Empty array is fine. */
  lines?: ReadonlyArray<TooltipLine>;
  /**
   * Optional flag to hide the tooltip without unmounting it. Defaults to
   * `false` (visible). Useful while the parent decides whether to render it.
   */
  hidden?: boolean;
}

/** Horizontal offset from the cursor, in CSS pixels, so the pointer is clear. */
const OFFSET_X = 12;
/** Vertical offset from the cursor, in CSS pixels. */
const OFFSET_Y = 12;

/**
 * Floating, pointer-events-none tooltip for the hovered cell.
 *
 * The panel is absolutely positioned within the viewport's relatively
 * positioned container. The parent is responsible for clamping `x`/`y` to the
 * container if edge avoidance is desired; this component simply offsets from the
 * given anchor.
 *
 * @param props - See {@link TooltipProps}.
 * @returns The tooltip element, or `null` when hidden.
 */
export function Tooltip(props: TooltipProps): JSX.Element | null {
  const { x, y, index, lines, hidden } = props;

  // Stabilize the style object so it is not reallocated on unrelated renders.
  const style = useMemo<CSSProperties>(
    () => ({
      left: x + OFFSET_X,
      top: y + OFFSET_Y,
    }),
    [x, y],
  );

  if (hidden) {
    return null;
  }

  return (
    <div
      className="pointer-events-none absolute z-20 max-w-xs select-none rounded-md border border-slate-700 bg-slate-900/95 px-2.5 py-1.5 text-xs text-slate-100 shadow-lg"
      style={style}
      role="tooltip"
      aria-hidden="true"
    >
      <div className="font-mono text-[11px] text-sky-300">cell #{index}</div>
      {lines && lines.length > 0 && (
        <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5">
          {lines.map((line) => (
            <div key={line.label} className="contents">
              <dt className="truncate text-slate-400">{line.label}</dt>
              <dd className="truncate text-right font-mono text-slate-100">
                {line.value}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

export default Tooltip;
