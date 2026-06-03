// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * Spatial selection helpers operating in *data space*.
 *
 * The viewport draws an interaction overlay in screen pixels (a drag rectangle
 * or a freehand lasso). To turn that into a set of selected cells we unproject
 * the screen polygon to data coordinates via the deck.gl viewport, then test
 * every cell position against it.
 *
 * Positions are the interleaved-xy `Float32Array` defined by CONTRACT §9.4
 * (`[x0, y0, x1, y1, ...]`, length `2 * n`). Results are an `Int32Array` of
 * FULL-dataset cell indices (CONTRACT §7: selection indexes into the full
 * dataset), matching the `Int32` cell-index dtype required by §9.3.
 *
 * These helpers are pure: no React, no store, no DOM.
 */

/**
 * Minimal structural view of a deck.gl viewport — just enough to unproject a
 * screen-space point to data coordinates. Typed loosely on purpose so callers
 * can pass an `OrthographicViewport` (or any compatible object) without a hard
 * dependency on deck.gl types in this pure module.
 */
export interface Unprojectable {
  /**
   * Convert a screen-space `[x, y]` (CSS pixels, origin top-left) to data-space
   * coordinates. deck.gl returns `[x, y]` (and optionally `z`); we read the
   * first two components.
   */
  unproject(xy: [number, number]): [number, number] | number[];
}

/** A screen-space axis-aligned rectangle in CSS pixels. */
export interface ScreenRect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/**
 * A screen-space polygon: a flat list of `[x, y]` vertex pairs in CSS pixels.
 * Open or closed (the ray-cast test treats it as implicitly closed).
 */
export type ScreenPolygon = ReadonlyArray<readonly [number, number]>;

/**
 * Test whether a point lies inside a polygon using the even-odd ray-casting
 * rule.
 *
 * The polygon is given as flat data-space coordinate arrays (`polyX[i]`,
 * `polyY[i]`) to avoid per-vertex object allocations in the inner loop. The
 * polygon is treated as implicitly closed (the last vertex connects back to the
 * first).
 *
 * @param px - X coordinate of the test point (data space).
 * @param py - Y coordinate of the test point (data space).
 * @param polyX - Polygon vertex X coordinates (data space).
 * @param polyY - Polygon vertex Y coordinates (data space).
 * @returns `true` if the point is inside the polygon.
 */
export function pointInPolygon(
  px: number,
  py: number,
  polyX: Float64Array,
  polyY: Float64Array,
): boolean {
  let inside = false;
  const n = polyX.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const xi = polyX[i];
    const yi = polyY[i];
    const xj = polyX[j];
    const yj = polyY[j];
    // Does the horizontal ray from (px, py) cross edge (i, j)?
    const intersects =
      yi > py !== yj > py &&
      px < ((xj - xi) * (py - yi)) / (yj - yi) + xi;
    if (intersects) {
      inside = !inside;
    }
  }
  return inside;
}

/**
 * Select all cells whose data-space position falls within a screen-space
 * rectangle.
 *
 * The rectangle's two corners are unprojected to data space; because the
 * orthographic view can be flipped on the Y axis, we normalize to min/max
 * bounds after unprojection rather than assuming `x0 < x1` / `y0 < y1`.
 *
 * @param positions - Interleaved-xy `Float32Array` of length `2 * n` (CONTRACT
 *   §9.4). May be `null`/empty, in which case an empty selection is returned.
 * @param viewport - Object exposing `unproject` (a deck.gl viewport).
 * @param screenRect - Drag rectangle in CSS pixels.
 * @returns `Int32Array` of matching full-dataset cell indices, in ascending
 *   order.
 */
export function selectInRect(
  positions: Float32Array | null | undefined,
  viewport: Unprojectable,
  screenRect: ScreenRect,
): Int32Array {
  if (!positions || positions.length === 0) {
    return new Int32Array(0);
  }

  const c0 = viewport.unproject([screenRect.x0, screenRect.y0]);
  const c1 = viewport.unproject([screenRect.x1, screenRect.y1]);

  const minX = Math.min(c0[0], c1[0]);
  const maxX = Math.max(c0[0], c1[0]);
  const minY = Math.min(c0[1], c1[1]);
  const maxY = Math.max(c0[1], c1[1]);

  const n = positions.length >>> 1;
  // Over-allocate to the worst case, then trim with subarray (no second pass).
  const matches = new Int32Array(n);
  let count = 0;
  for (let i = 0; i < n; i++) {
    const x = positions[i * 2];
    const y = positions[i * 2 + 1];
    if (x >= minX && x <= maxX && y >= minY && y <= maxY) {
      matches[count++] = i;
    }
  }
  return matches.subarray(0, count);
}

/**
 * Select all cells whose data-space position falls within a screen-space
 * polygon (freehand lasso).
 *
 * Each polygon vertex is unprojected once to data space, then every cell is
 * tested with {@link pointInPolygon}. A polygon with fewer than three vertices
 * encloses no area and yields an empty selection.
 *
 * @param positions - Interleaved-xy `Float32Array` of length `2 * n` (CONTRACT
 *   §9.4). May be `null`/empty, in which case an empty selection is returned.
 * @param viewport - Object exposing `unproject` (a deck.gl viewport).
 * @param screenPolygon - Lasso vertices in CSS pixels (open; treated as closed).
 * @returns `Int32Array` of matching full-dataset cell indices, in ascending
 *   order.
 */
export function selectInPolygon(
  positions: Float32Array | null | undefined,
  viewport: Unprojectable,
  screenPolygon: ScreenPolygon,
): Int32Array {
  if (!positions || positions.length === 0 || screenPolygon.length < 3) {
    return new Int32Array(0);
  }

  // Unproject all polygon vertices once into flat data-space arrays so the
  // hot per-cell loop touches only typed arrays (no object property reads).
  const m = screenPolygon.length;
  const polyX = new Float64Array(m);
  const polyY = new Float64Array(m);
  for (let k = 0; k < m; k++) {
    const v = screenPolygon[k];
    const d = viewport.unproject([v[0], v[1]]);
    polyX[k] = d[0];
    polyY[k] = d[1];
  }

  const n = positions.length >>> 1;
  const matches = new Int32Array(n);
  let count = 0;
  for (let i = 0; i < n; i++) {
    const x = positions[i * 2];
    const y = positions[i * 2 + 1];
    if (pointInPolygon(x, y, polyX, polyY)) {
      matches[count++] = i;
    }
  }
  return matches.subarray(0, count);
}
