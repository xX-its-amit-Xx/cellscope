// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * EmbeddingViewport — the GPU-accelerated scatter plot of the cell embedding.
 *
 * This is the performance-critical surface of CellScope. It renders up to a few
 * million cells at interactive frame rates by feeding deck.gl *binary
 * attributes* directly (CONTRACT section 9.5): the interleaved-xy `Float32Array`
 * positions and the `Uint8Array` RGB fill-color buffer go to the GPU with no
 * per-row JavaScript objects.
 *
 * Responsibilities:
 * - One {@link ScatterplotLayer} fed binary attributes, rendered under an
 *   {@link OrthographicView} with a pan/zoom controller, initially fit to the
 *   embedding bounds.
 * - Optional strided downsampling for very large datasets, with a sampled→full
 *   index map so picking and selection still resolve to FULL-dataset indices.
 * - Hover wiring into the store + a positioned {@link Tooltip}.
 * - An SVG selection overlay (box / lasso) that unprojects to data space and
 *   computes a FULL-dataset selection via the pure helpers in `../lib/selection`.
 * - A best-effort second layer for recomputed-UMAP overlay coordinates.
 *
 * All typed arrays are memoized; nothing allocates per frame or per point in the
 * render path.
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type PointerEvent as ReactPointerEvent,
  type Ref,
} from 'react';
import DeckGL, { type DeckGLRef } from 'deck.gl';
import { OrthographicView, type PickingInfo } from '@deck.gl/core';
import { ScatterplotLayer } from '@deck.gl/layers';

import { useStore } from '../store/useStore';
import { useColorBuffer } from '../hooks/useColorBuffer';
import {
  selectInPolygon,
  selectInRect,
  type ScreenRect,
  type Unprojectable,
} from '../lib/selection';
import { Tooltip, type TooltipLine } from './Tooltip';
import type { SelectionTool } from '../types';

/** Constant point radius, in CSS pixels. */
const POINT_RADIUS_PX = 2;
/** Minimum on-screen point radius so points never vanish when zoomed out. */
const RADIUS_MIN_PX = 1;
/** Maximum on-screen point radius so single points do not balloon. */
const RADIUS_MAX_PX = 6;
/** Fraction of the data span left as margin when fitting bounds. */
const FIT_PADDING = 0.05;
/** Minimum drag distance (CSS px) before a box/lasso drag is treated as real. */
const DRAG_THRESHOLD_PX = 3;

/** RGB color for recomputed-overlay points (a warm highlight). */
const OVERLAY_COLOR: [number, number, number, number] = [255, 140, 0, 255];

/**
 * An orthographic view state. deck.gl's `OrthographicView` is centered on
 * `target` (data coords) with `zoom` as log2 pixels-per-unit.
 */
interface OrthoViewState {
  /** Center of the view in data coordinates `[x, y, z?]`. */
  target: [number, number, number];
  /** Log2 zoom (pixels per data unit = `2 ** zoom`). */
  zoom: number;
  /** Minimum zoom bound. */
  minZoom: number;
  /** Maximum zoom bound. */
  maxZoom: number;
}

/** A screen-space point in CSS pixels relative to the container. */
interface ScreenPoint {
  x: number;
  y: number;
}

/**
 * Minimal structural view of the underlying deck.gl `Deck` instance exposed by
 * the React `DeckGL` wrapper's ref as `.deck`. We only need `getViewports`,
 * which returns objects implementing `unproject` (see {@link Unprojectable}).
 */
interface DeckHandle {
  /** The live `Deck` instance, present once the canvas has initialized. */
  deck?: {
    /** Current viewports for the configured views. */
    getViewports?: () => Array<Unprojectable>;
  };
}

/**
 * In-progress overlay drag state. `box` keeps two corners; `lasso` accumulates
 * a freehand path. Stored in a ref (not React state) for the path points so the
 * high-frequency `pointermove` updates do not thrash React, with a small
 * `version` counter in state to trigger redraws.
 */
interface DragState {
  tool: Exclude<SelectionTool, 'pan'>;
  start: ScreenPoint;
  current: ScreenPoint;
  path: ScreenPoint[];
  moved: boolean;
}

/**
 * Compute an orthographic view state that fits the given data bounds into a
 * container of the given pixel size, with a small margin.
 *
 * @param bounds - Data-space bounds `[minX, minY, maxX, maxY]`.
 * @param width - Container width in CSS pixels.
 * @param height - Container height in CSS pixels.
 * @returns A fitted {@link OrthoViewState}.
 */
function fitBounds(
  bounds: readonly [number, number, number, number],
  width: number,
  height: number,
): OrthoViewState {
  const [minX, minY, maxX, maxY] = bounds;
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;

  const spanX = Math.max(maxX - minX, 1e-6);
  const spanY = Math.max(maxY - minY, 1e-6);
  const padX = spanX * (1 + FIT_PADDING * 2);
  const padY = spanY * (1 + FIT_PADDING * 2);

  const w = Math.max(width, 1);
  const h = Math.max(height, 1);

  // Pixels-per-unit that makes the padded span fit each axis; take the smaller
  // so both axes are visible. zoom = log2(pixels per unit).
  const ppuX = w / padX;
  const ppuY = h / padY;
  const ppu = Math.min(ppuX, ppuY);
  const zoom = Math.log2(ppu);

  return {
    target: [cx, cy, 0],
    zoom,
    minZoom: zoom - 8,
    maxZoom: zoom + 16,
  };
}

/**
 * Build a strided sample of cell indices for downsampled rendering.
 *
 * Returns the mapping from rendered (sampled) index → full-dataset index. The
 * stride is `ceil(n / target)` so the rendered count is at most `target`.
 *
 * @param n - Full dataset cell count.
 * @param target - Desired maximum rendered count.
 * @returns An `Int32Array` mapping sampled index → full index.
 */
function buildSampleMap(n: number, target: number): Int32Array {
  const stride = Math.max(1, Math.ceil(n / Math.max(1, target)));
  const count = Math.ceil(n / stride);
  const map = new Int32Array(count);
  let j = 0;
  for (let i = 0; i < n; i += stride) {
    map[j] = i;
    j += 1;
  }
  return map;
}

/**
 * Gather a strided subset of an interleaved-xy `Float32Array` (`size: 2`) using
 * a sampled→full index map.
 *
 * @param positions - Full interleaved-xy positions, length `2 * n`.
 * @param map - Sampled→full index map.
 * @returns A freshly allocated interleaved-xy `Float32Array`, length `2 * map.length`.
 */
function gatherPositions(positions: Float32Array, map: Int32Array): Float32Array {
  const out = new Float32Array(map.length * 2);
  for (let s = 0; s < map.length; s += 1) {
    const f = map[s];
    out[s * 2] = positions[f * 2];
    out[s * 2 + 1] = positions[f * 2 + 1];
  }
  return out;
}

/**
 * Gather a strided subset of an RGB `Uint8Array` (`size: 3`) using a
 * sampled→full index map.
 *
 * @param colors - Full RGB buffer, length `3 * n`.
 * @param map - Sampled→full index map.
 * @returns A freshly allocated RGB `Uint8Array`, length `3 * map.length`.
 */
function gatherColors(colors: Uint8Array, map: Int32Array): Uint8Array {
  const out = new Uint8Array(map.length * 3);
  for (let s = 0; s < map.length; s += 1) {
    const f = map[s];
    const fo = f * 3;
    const so = s * 3;
    out[so] = colors[fo];
    out[so + 1] = colors[fo + 1];
    out[so + 2] = colors[fo + 2];
  }
  return out;
}

/**
 * The main embedding scatter viewport.
 *
 * Reads positions, color state, view options, and the active selection tool
 * from the Zustand store; writes hover and selection back to it.
 *
 * @returns The viewport element. Renders an informational placeholder when no
 *   embedding is loaded.
 */
export function EmbeddingViewport(): JSX.Element {
  const positions = useStore((s) => s.positions);
  const bounds = useStore((s) => s.bounds);
  const nObs = useStore((s) => s.nObs);

  const colorKind = useStore((s) => s.colorKind);
  const colorValues = useStore((s) => s.colorValues);
  const colorCodes = useStore((s) => s.colorCodes);
  const colorDomain = useStore((s) => s.colorDomain);
  const categories = useStore((s) => s.categories);
  const colormapName = useStore((s) => s.colormapName);
  const reclusterLabels = useStore((s) => s.reclusterLabels);

  const downsample = useStore((s) => s.downsample);
  const downsampleN = useStore((s) => s.downsampleN);
  const activeTool = useStore((s) => s.activeTool);

  const selection = useStore((s) => s.selection);
  const recomputedPositions = useStore((s) => s.recomputedPositions);

  const setHovered = useStore((s) => s.setHovered);
  const setSelection = useStore((s) => s.setSelection);
  const clearSelection = useStore((s) => s.clearSelection);

  // Container sizing for fit-to-bounds and overlay coordinate math.
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState<{ width: number; height: number }>({
    width: 0,
    height: 0,
  });

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    const update = (): void => {
      setSize({ width: el.clientWidth, height: el.clientHeight });
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  /**
   * Color buffer for the FULL dataset (length `nObs * 3`). When a recluster
   * result is present we color by those ephemeral labels (categorical path);
   * otherwise we use the active color state. The hook handles the
   * none/continuous/categorical cases and gray fallbacks.
   */
  const effectiveColorCodes = reclusterLabels ?? colorCodes;
  const effectiveColorKind = reclusterLabels ? 'categorical' : colorKind;
  const fullColorBuffer = useColorBuffer({
    nObs,
    colorKind: effectiveColorKind,
    colorValues,
    colorCodes: effectiveColorCodes,
    colorDomain,
    categories,
    colormapName,
  });

  // Decide whether we are downsampling and build the sampled→full index map.
  const isDownsampling = downsample && nObs > downsampleN;
  const sampleMap = useMemo<Int32Array | null>(() => {
    if (!isDownsampling) return null;
    return buildSampleMap(nObs, downsampleN);
  }, [isDownsampling, nObs, downsampleN]);

  // Rendered positions: either the full buffer or a gathered strided sample.
  const renderPositions = useMemo<Float32Array | null>(() => {
    if (!positions) return null;
    if (!sampleMap) return positions;
    return gatherPositions(positions, sampleMap);
  }, [positions, sampleMap]);

  // Rendered colors: either the full buffer or a gathered strided sample.
  const renderColors = useMemo<Uint8Array>(() => {
    if (!sampleMap) return fullColorBuffer;
    return gatherColors(fullColorBuffer, sampleMap);
  }, [fullColorBuffer, sampleMap]);

  // Number of points actually rendered.
  const renderCount = sampleMap ? sampleMap.length : nObs;

  /**
   * Monotonic version bumped whenever the rendered color buffer changes, used as
   * the deck.gl `updateTriggers.getFillColor` key so the GPU buffer is uploaded
   * only on recolor (not every render).
   */
  const colorVersion = useMemo(
    () => ({ buffer: renderColors }),
    [renderColors],
  );

  // Controlled view state, (re)fit when the bounds or container size change.
  const [viewState, setViewState] = useState<OrthoViewState | null>(null);
  // Track which bounds/size we last auto-fit to so user pan/zoom is preserved.
  const fitKeyRef = useRef<string>('');

  useEffect(() => {
    if (!bounds || size.width === 0 || size.height === 0) return;
    const key = `${bounds.join(',')}|${size.width}x${size.height}`;
    if (key === fitKeyRef.current) return;
    fitKeyRef.current = key;
    setViewState(fitBounds(bounds, size.width, size.height));
  }, [bounds, size.width, size.height]);

  const onViewStateChange = useCallback(
    (params: { viewState: OrthoViewState }): void => {
      setViewState(params.viewState);
    },
    [],
  );

  /* ---------------------------------------------------------------------- */
  /* Hover                                                                  */
  /* ---------------------------------------------------------------------- */

  const [hoverScreen, setHoverScreen] = useState<ScreenPoint | null>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const onHover = useCallback(
    (info: PickingInfo): void => {
      if (info.index >= 0 && info.index < renderCount) {
        const fullIndex = sampleMap ? sampleMap[info.index] : info.index;
        setHoverIndex(fullIndex);
        setHoverScreen({ x: info.x, y: info.y });
        setHovered({ index: fullIndex });
      } else {
        setHoverIndex(null);
        setHoverScreen(null);
        setHovered(null);
      }
    },
    [renderCount, sampleMap, setHovered],
  );

  // Tooltip info lines: show the active color value for the hovered FULL cell.
  const tooltipLines = useMemo<TooltipLine[]>(() => {
    if (hoverIndex === null) return [];
    const lines: TooltipLine[] = [];
    if (
      effectiveColorKind === 'categorical' &&
      effectiveColorCodes &&
      hoverIndex < effectiveColorCodes.length
    ) {
      const code = effectiveColorCodes[hoverIndex];
      const label =
        code >= 0 && categories && code < categories.length
          ? categories[code]
          : code < 0
            ? 'n/a'
            : String(code);
      lines.push({ label: reclusterLabels ? 'cluster' : 'group', value: label });
    } else if (
      effectiveColorKind === 'continuous' &&
      colorValues &&
      hoverIndex < colorValues.length
    ) {
      const v = colorValues[hoverIndex];
      lines.push({
        label: 'value',
        value: Number.isFinite(v) ? v.toPrecision(4) : 'NaN',
      });
    }
    return lines;
  }, [
    hoverIndex,
    effectiveColorKind,
    effectiveColorCodes,
    colorValues,
    categories,
    reclusterLabels,
  ]);

  /* ---------------------------------------------------------------------- */
  /* Selection overlay (box / lasso)                                         */
  /* ---------------------------------------------------------------------- */

  const deckRef = useRef<DeckHandle | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const [dragVersion, setDragVersion] = useState(0);
  const isSelecting = activeTool === 'box' || activeTool === 'lasso';
  // Disable deck's drag-pan while a selection tool is armed so the overlay owns
  // the drag gesture.
  const controllerDragPan = !isSelecting;

  /**
   * Resolve a deck.gl viewport exposing `unproject` for the current view. We
   * read it from the live DeckGL instance via its ref so the unprojection
   * matches exactly what is on screen (including any in-flight transitions).
   *
   * @returns An {@link Unprojectable}, or `null` if unavailable.
   */
  const getViewport = useCallback((): Unprojectable | null => {
    const deck = deckRef.current?.deck;
    if (deck && typeof deck.getViewports === 'function') {
      const vps = deck.getViewports();
      if (vps && vps.length > 0) {
        return vps[0];
      }
    }
    return null;
  }, []);

  const pointerPos = useCallback((evt: ReactPointerEvent): ScreenPoint => {
    const rect = containerRef.current?.getBoundingClientRect();
    return {
      x: evt.clientX - (rect?.left ?? 0),
      y: evt.clientY - (rect?.top ?? 0),
    };
  }, []);

  const onPointerDown = useCallback(
    (evt: ReactPointerEvent): void => {
      if (!isSelecting || evt.button !== 0) return;
      const p = pointerPos(evt);
      dragRef.current = {
        tool: activeTool as Exclude<SelectionTool, 'pan'>,
        start: p,
        current: p,
        path: [p],
        moved: false,
      };
      (evt.currentTarget as Element).setPointerCapture(evt.pointerId);
      setDragVersion((v) => v + 1);
    },
    [isSelecting, activeTool, pointerPos],
  );

  const onPointerMove = useCallback(
    (evt: ReactPointerEvent): void => {
      const drag = dragRef.current;
      if (!drag) return;
      const p = pointerPos(evt);
      drag.current = p;
      const dx = p.x - drag.start.x;
      const dy = p.y - drag.start.y;
      if (!drag.moved && dx * dx + dy * dy >= DRAG_THRESHOLD_PX * DRAG_THRESHOLD_PX) {
        drag.moved = true;
      }
      if (drag.tool === 'lasso') {
        drag.path.push(p);
      }
      setDragVersion((v) => v + 1);
    },
    [pointerPos],
  );

  const finishDrag = useCallback(
    (evt: ReactPointerEvent): void => {
      const drag = dragRef.current;
      dragRef.current = null;
      if (!drag) return;
      try {
        (evt.currentTarget as Element).releasePointerCapture(evt.pointerId);
      } catch {
        // Pointer capture may already be released; ignore.
      }

      if (drag.moved && positions) {
        const viewport = getViewport();
        if (viewport) {
          if (drag.tool === 'box') {
            const rect: ScreenRect = {
              x0: drag.start.x,
              y0: drag.start.y,
              x1: drag.current.x,
              y1: drag.current.y,
            };
            const result = selectInRect(positions, viewport, rect);
            if (result.length > 0) {
              void setSelection(result);
            } else {
              void clearSelection();
            }
          } else {
            const poly = drag.path.map(
              (pt): [number, number] => [pt.x, pt.y],
            );
            const result = selectInPolygon(positions, viewport, poly);
            if (result.length > 0) {
              void setSelection(result);
            } else {
              void clearSelection();
            }
          }
        }
      }
      setDragVersion((v) => v + 1);
    },
    [positions, getViewport, setSelection, clearSelection],
  );

  // SVG overlay geometry for the active drag.
  const overlay = useMemo(() => {
    void dragVersion; // dependency: redraw when the drag advances
    const drag = dragRef.current;
    if (!drag || !drag.moved) return null;
    if (drag.tool === 'box') {
      const x = Math.min(drag.start.x, drag.current.x);
      const y = Math.min(drag.start.y, drag.current.y);
      const w = Math.abs(drag.current.x - drag.start.x);
      const h = Math.abs(drag.current.y - drag.start.y);
      return { kind: 'box' as const, x, y, w, h };
    }
    const pts = drag.path.map((p) => `${p.x},${p.y}`).join(' ');
    return { kind: 'lasso' as const, points: pts };
  }, [dragVersion]);

  /* ---------------------------------------------------------------------- */
  /* Layers                                                                  */
  /* ---------------------------------------------------------------------- */

  const layers = useMemo(() => {
    const result: ScatterplotLayer[] = [];
    if (renderPositions && renderCount > 0) {
      result.push(
        new ScatterplotLayer({
          id: 'cells',
          data: {
            length: renderCount,
            attributes: {
              getPosition: { value: renderPositions, size: 2 },
              getFillColor: { value: renderColors, size: 3 },
            },
          },
          radiusUnits: 'pixels',
          getRadius: POINT_RADIUS_PX,
          radiusMinPixels: RADIUS_MIN_PX,
          radiusMaxPixels: RADIUS_MAX_PX,
          pickable: true,
          // Recolor without rebuilding the layer or re-uploading positions.
          updateTriggers: {
            getFillColor: colorVersion,
          },
        }),
      );
    }

    // Optional recomputed-UMAP overlay for the selected subset. Kept simple: a
    // single uniformly colored layer of the new coordinates. Defensive against
    // an empty/odd-length buffer so a malformed result cannot crash the view.
    if (
      recomputedPositions &&
      recomputedPositions.length >= 2 &&
      recomputedPositions.length % 2 === 0
    ) {
      const overlayCount = recomputedPositions.length >>> 1;
      result.push(
        new ScatterplotLayer({
          id: 'recomputed-overlay',
          data: {
            length: overlayCount,
            attributes: {
              getPosition: { value: recomputedPositions, size: 2 },
            },
          },
          getFillColor: OVERLAY_COLOR,
          radiusUnits: 'pixels',
          getRadius: POINT_RADIUS_PX + 1,
          radiusMinPixels: RADIUS_MIN_PX,
          radiusMaxPixels: RADIUS_MAX_PX + 2,
          pickable: false,
        }),
      );
    }

    return result;
  }, [renderPositions, renderCount, renderColors, colorVersion, recomputedPositions]);

  // The view does not enable its own controller; the controller is supplied via
  // the DeckGL-level `controller` prop so its `dragPan` flag can be toggled while
  // a selection tool owns the drag gesture.
  const views = useMemo(() => new OrthographicView({ id: 'ortho' }), []);

  const controller = useMemo(
    () => ({ dragPan: controllerDragPan, dragRotate: false, scrollZoom: true }),
    [controllerDragPan],
  );

  /* ---------------------------------------------------------------------- */
  /* Render                                                                  */
  /* ---------------------------------------------------------------------- */

  const hasData = Boolean(positions && bounds && nObs > 0);

  return (
    <div ref={containerRef} className="relative h-full w-full overflow-hidden bg-slate-950">
      {hasData && viewState ? (
        <>
          <DeckGL
            ref={deckRef as unknown as Ref<DeckGLRef<OrthographicView>>}
            views={views}
            viewState={viewState}
            controller={controller}
            onViewStateChange={
              onViewStateChange as unknown as ComponentProps<
                typeof DeckGL
              >['onViewStateChange']
            }
            layers={layers}
            onHover={onHover}
            getCursor={() => (isSelecting ? 'crosshair' : 'grab')}
            style={{ position: 'absolute', inset: '0' }}
          />

          {/* Selection overlay: captures the drag gesture while a tool is armed. */}
          {isSelecting && (
            <svg
              className="absolute inset-0 z-10 h-full w-full cursor-crosshair"
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={finishDrag}
              onPointerCancel={finishDrag}
            >
              {overlay && overlay.kind === 'box' && (
                <rect
                  x={overlay.x}
                  y={overlay.y}
                  width={overlay.w}
                  height={overlay.h}
                  fill="rgba(56, 189, 248, 0.12)"
                  stroke="rgb(56, 189, 248)"
                  strokeWidth={1.5}
                />
              )}
              {overlay && overlay.kind === 'lasso' && (
                <polyline
                  points={overlay.points}
                  fill="rgba(56, 189, 248, 0.12)"
                  stroke="rgb(56, 189, 248)"
                  strokeWidth={1.5}
                  strokeLinejoin="round"
                />
              )}
            </svg>
          )}

          {/* Hover tooltip (pointer-events: none). */}
          {hoverIndex !== null && hoverScreen && !isSelecting && (
            <Tooltip
              x={hoverScreen.x}
              y={hoverScreen.y}
              index={hoverIndex}
              lines={tooltipLines}
            />
          )}

          {/* Downsampling indicator. */}
          {isDownsampling && (
            <div className="pointer-events-none absolute bottom-2 left-2 z-10 rounded bg-slate-900/80 px-2 py-1 text-[11px] text-slate-300">
              showing {renderCount.toLocaleString('en-US')} of{' '}
              {nObs.toLocaleString('en-US')} cells
            </div>
          )}

          {selection && selection.length > 0 && (
            <div className="pointer-events-none absolute bottom-2 right-2 z-10 rounded bg-sky-900/80 px-2 py-1 text-[11px] text-sky-100">
              {selection.length.toLocaleString('en-US')} selected
            </div>
          )}
        </>
      ) : (
        <div className="flex h-full w-full items-center justify-center text-sm text-slate-500">
          {nObs > 0 ? 'Preparing embedding…' : 'No embedding loaded.'}
        </div>
      )}
    </div>
  );
}

export default EmbeddingViewport;
