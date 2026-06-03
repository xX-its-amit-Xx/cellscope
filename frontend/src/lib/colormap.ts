// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * Color mapping utilities for the scatter viewport.
 *
 * Provides continuous colormaps (viridis, magma, coolwarm) and a categorical
 * palette, all implemented locally via small control-point tables plus linear
 * interpolation. No external dependencies — these run in the hot path that
 * builds per-cell GPU color buffers, so they must be cheap and allocation-free
 * per call.
 *
 * Colors are returned as `[r, g, b]` triples with each channel in the inclusive
 * range `0..255` (integers), matching the `Uint8Array` `getFillColor` buffer
 * consumed by deck.gl (see CONTRACT §9.5).
 */

/** An RGB color, each channel an integer in `[0, 255]`. */
export type RGB = [number, number, number];

/**
 * A continuous colormap expressed as evenly spaced control points.
 *
 * Each entry is an `[r, g, b]` triple in `0..255`. The first entry maps to
 * `t = 0`, the last to `t = 1`, and intermediate values are produced by linear
 * interpolation between the two bracketing control points.
 */
type ControlPoints = readonly RGB[];

/**
 * Viridis (perceptually uniform, colorblind-friendly). Sampled control points
 * from the matplotlib reference colormap.
 */
const VIRIDIS: ControlPoints = [
  [68, 1, 84],
  [72, 26, 108],
  [71, 47, 125],
  [65, 68, 135],
  [57, 86, 140],
  [49, 104, 142],
  [42, 120, 142],
  [35, 136, 142],
  [31, 152, 139],
  [34, 168, 132],
  [53, 183, 121],
  [84, 197, 104],
  [122, 209, 81],
  [165, 219, 54],
  [210, 226, 27],
  [253, 231, 37],
];

/**
 * Magma (perceptually uniform, dark-to-light). Sampled control points from the
 * matplotlib reference colormap.
 */
const MAGMA: ControlPoints = [
  [0, 0, 4],
  [12, 8, 38],
  [28, 16, 68],
  [52, 16, 104],
  [79, 18, 123],
  [104, 28, 129],
  [129, 37, 129],
  [155, 46, 127],
  [181, 54, 122],
  [207, 68, 112],
  [229, 87, 98],
  [245, 113, 91],
  [252, 144, 96],
  [254, 177, 116],
  [254, 209, 146],
  [252, 253, 191],
];

/**
 * Coolwarm (diverging blue-white-red). Sampled control points from the
 * matplotlib reference colormap; useful for signed/centered values.
 */
const COOLWARM: ControlPoints = [
  [59, 76, 192],
  [77, 104, 215],
  [98, 130, 234],
  [119, 154, 247],
  [141, 176, 254],
  [163, 194, 255],
  [184, 208, 249],
  [204, 217, 238],
  [221, 221, 221],
  [236, 211, 197],
  [245, 196, 173],
  [250, 176, 151],
  [251, 154, 128],
  [247, 130, 107],
  [238, 102, 87],
  [221, 70, 70],
  [180, 4, 38],
];

/** Registry of available continuous colormaps, keyed by lowercase name. */
const COLORMAPS: Readonly<Record<string, ControlPoints>> = {
  viridis: VIRIDIS,
  magma: MAGMA,
  coolwarm: COOLWARM,
};

/** Default colormap used when an unknown name is requested. */
const DEFAULT_COLORMAP = "viridis";

/**
 * Categorical palette (20 distinct colors, tab20-like) for discrete groupings
 * such as Leiden clusters or cell-type labels. Colors cycle by index modulo the
 * palette length, so any non-negative category code resolves to a stable color.
 */
const CATEGORICAL_PALETTE: readonly RGB[] = [
  [31, 119, 180],
  [174, 199, 232],
  [255, 127, 14],
  [255, 187, 120],
  [44, 160, 44],
  [152, 223, 138],
  [214, 39, 40],
  [255, 152, 150],
  [148, 103, 189],
  [197, 176, 213],
  [140, 86, 75],
  [196, 156, 148],
  [227, 119, 194],
  [247, 182, 210],
  [127, 127, 127],
  [199, 199, 199],
  [188, 189, 34],
  [219, 219, 141],
  [23, 190, 207],
  [158, 218, 229],
];

/**
 * Clamp a number into the inclusive range `[lo, hi]`.
 *
 * @param value - Input value.
 * @param lo - Lower bound.
 * @param hi - Upper bound.
 * @returns `value` constrained to `[lo, hi]`.
 */
function clamp(value: number, lo: number, hi: number): number {
  return value < lo ? lo : value > hi ? hi : value;
}

/**
 * Sample a continuous colormap at a normalized position.
 *
 * @param name - Colormap name (case-insensitive). Unknown names fall back to
 *   `viridis`.
 * @param t - Normalized position in `[0, 1]`. Values outside the range are
 *   clamped. `NaN` is treated as `0`.
 * @returns An `[r, g, b]` triple with each channel an integer in `[0, 255]`.
 */
export function continuousColor(name: string, t: number): RGB {
  const points = COLORMAPS[name?.toLowerCase()] ?? COLORMAPS[DEFAULT_COLORMAP];
  const safeT = Number.isFinite(t) ? clamp(t, 0, 1) : 0;

  const segments = points.length - 1;
  const scaled = safeT * segments;
  // Index of the lower control point bracketing `scaled`. Clamp so that
  // safeT === 1 selects the final segment rather than reading past the array.
  const lo = Math.min(Math.floor(scaled), segments - 1);
  const frac = scaled - lo;

  const a = points[lo];
  const b = points[lo + 1];

  return [
    Math.round(a[0] + (b[0] - a[0]) * frac),
    Math.round(a[1] + (b[1] - a[1]) * frac),
    Math.round(a[2] + (b[2] - a[2]) * frac),
  ];
}

/**
 * Look up the categorical color for a category index.
 *
 * @param i - Category index. Negative indices (e.g. the `-1` missing-code
 *   sentinel) are the caller's responsibility to special-case; this function
 *   wraps any integer into the palette by modulo, so `-1` is not gray here.
 * @returns An `[r, g, b]` triple from the categorical palette.
 */
export function categoryColor(i: number): RGB {
  const n = CATEGORICAL_PALETTE.length;
  // Euclidean-style modulo so negative indices still map to a valid slot.
  const idx = ((Math.trunc(i) % n) + n) % n;
  return CATEGORICAL_PALETTE[idx];
}

/**
 * List the names of all registered continuous colormaps.
 *
 * @returns A new array of colormap names (lowercase), suitable for populating a
 *   colormap selector in the UI.
 */
export function listColormaps(): string[] {
  return Object.keys(COLORMAPS);
}

/** Number of distinct colors in the categorical palette. */
export const CATEGORICAL_PALETTE_SIZE = CATEGORICAL_PALETTE.length;
