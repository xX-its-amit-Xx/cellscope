// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * Hook that builds the per-cell fill-color buffer consumed by deck.gl.
 *
 * The result is a `Uint8Array` of length `n * 3` (`[r0, g0, b0, r1, g1, b1,
 * ...]`) fed directly to the `ScatterplotLayer` `getFillColor` binary attribute
 * with `size: 3` (CONTRACT §9.5). Building it here — rather than storing it —
 * keeps the store free of derived GPU data (CONTRACT §7).
 *
 * Coloring follows the store's `colorKind` (CONTRACT §7):
 * - `none`        → every cell is a neutral default gray.
 * - `continuous`  → normalize each value across `colorDomain`, map through the
 *                   named colormap; `NaN`/non-finite → gray.
 * - `categorical` → color by category code; the `-1` missing sentinel → gray.
 *
 * The buffer is recomputed only when its inputs change (see the dependency
 * list of the underlying `useMemo`).
 */

import { useMemo } from "react";

import { categoryColor, continuousColor } from "../lib/colormap";

/** Color kind, mirroring `colorKind` in the Zustand store (CONTRACT §7). */
export type ColorKind = "none" | "continuous" | "categorical";

/**
 * Inputs to the color-buffer computation. These mirror the relevant slice of
 * the Zustand store (CONTRACT §7) plus the cell count.
 */
export interface ColorBufferInput {
  /** Number of cells (`nObs`). Determines the buffer length (`n * 3`). */
  nObs: number;
  /** Active color kind. */
  colorKind: ColorKind;
  /** Continuous per-cell values (`Float32`), used when `colorKind` is `continuous`. */
  colorValues: Float32Array | null;
  /** Categorical per-cell codes (`Int32`), used when `colorKind` is `categorical`. */
  colorCodes: Int32Array | null;
  /** `[min, max]` domain for continuous normalization. */
  colorDomain: [number, number] | null;
  /** Category labels. Accepted for store-shape parity; not read by the buffer (color depends only on codes). */
  categories: string[] | null;
  /** Name of the continuous colormap (e.g. `"viridis"`). */
  colormapName: string;
}

/** Neutral gray used for the "no coloring", NaN, and missing-code cases. */
const GRAY_R = 180;
const GRAY_G = 180;
const GRAY_B = 180;

/**
 * Fill a contiguous buffer with a single solid color (used for the `none` mode
 * and as the default before specialized fills run).
 *
 * @param buf - Destination buffer of length `n * 3`.
 * @param r - Red channel `0..255`.
 * @param g - Green channel `0..255`.
 * @param b - Blue channel `0..255`.
 */
function fillSolid(buf: Uint8Array, r: number, g: number, b: number): void {
  for (let i = 0; i < buf.length; i += 3) {
    buf[i] = r;
    buf[i + 1] = g;
    buf[i + 2] = b;
  }
}

/**
 * Build the fill-color buffer from the current color state.
 *
 * Allocates exactly one `Uint8Array(n * 3)`. The hot loops touch only typed
 * arrays and primitive locals — no per-cell object allocation — to keep recolor
 * latency low for multi-million-cell datasets.
 *
 * @param input - The color state slice plus `nObs`.
 * @returns A memoized `Uint8Array` of length `nObs * 3`, recomputed only when
 *   the inputs change. When `nObs <= 0` an empty array is returned.
 */
export function useColorBuffer(input: ColorBufferInput): Uint8Array {
  const {
    nObs,
    colorKind,
    colorValues,
    colorCodes,
    colorDomain,
    colormapName,
  } = input;

  return useMemo<Uint8Array>(() => {
    const n = nObs > 0 ? nObs : 0;
    const buf = new Uint8Array(n * 3);
    if (n === 0) {
      return buf;
    }

    // Continuous coloring: normalize each value across the domain and sample.
    if (colorKind === "continuous" && colorValues && colorDomain) {
      const [vmin, vmax] = colorDomain;
      const range = vmax - vmin;
      const invRange = range > 0 ? 1 / range : 0;
      const count = Math.min(n, colorValues.length);

      for (let i = 0; i < count; i++) {
        const v = colorValues[i];
        if (Number.isFinite(v)) {
          // invRange === 0 (degenerate domain) maps everything to t = 0.
          const t = invRange === 0 ? 0 : (v - vmin) * invRange;
          const [r, g, b] = continuousColor(colormapName, t);
          const o = i * 3;
          buf[o] = r;
          buf[o + 1] = g;
          buf[o + 2] = b;
        } else {
          const o = i * 3;
          buf[o] = GRAY_R;
          buf[o + 1] = GRAY_G;
          buf[o + 2] = GRAY_B;
        }
      }
      // Any cells past `colorValues.length` (shouldn't happen) stay gray.
      for (let i = count; i < n; i++) {
        const o = i * 3;
        buf[o] = GRAY_R;
        buf[o + 1] = GRAY_G;
        buf[o + 2] = GRAY_B;
      }
      return buf;
    }

    // Categorical coloring: color by code; -1 (missing/NaN) → gray.
    if (colorKind === "categorical" && colorCodes) {
      const count = Math.min(n, colorCodes.length);
      for (let i = 0; i < count; i++) {
        const code = colorCodes[i];
        const o = i * 3;
        if (code < 0) {
          buf[o] = GRAY_R;
          buf[o + 1] = GRAY_G;
          buf[o + 2] = GRAY_B;
        } else {
          const [r, g, b] = categoryColor(code);
          buf[o] = r;
          buf[o + 1] = g;
          buf[o + 2] = b;
        }
      }
      for (let i = count; i < n; i++) {
        const o = i * 3;
        buf[o] = GRAY_R;
        buf[o + 1] = GRAY_G;
        buf[o + 2] = GRAY_B;
      }
      return buf;
    }

    // `none` (or incomplete state): uniform default gray.
    fillSolid(buf, GRAY_R, GRAY_G, GRAY_B);
    return buf;
    // `categories` is intentionally excluded: the color output depends only on
    // the integer codes, not the label text, so a relabel needs no recompute.
  }, [
    nObs,
    colorKind,
    colorValues,
    colorCodes,
    colorDomain,
    colormapName,
  ]);
}
