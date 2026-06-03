# CellScope — Performance

> This is the **honest** performance story. It states the methodology you *would* use
> to benchmark CellScope, the **design targets** the architecture is built to hit, and
> — explicitly — what was **not** measured here and why. The numbers below the
> "measured" line are **projections / design targets**, not benchmark results. The
> authoritative interface is [`CONTRACT.md`](./CONTRACT.md); the wire protocol that
> drives these numbers is restated in [`protocol.md`](./protocol.md) and the data-flow
> in [`architecture.md`](./architecture.md). Section references ("§4.5", "§9") point at
> `CONTRACT.md`.

---

## 1. TL;DR

- The render path is built to push **millions of points at interactive frame rates**
  by sending coordinates and colors as raw little-endian typed arrays (§9) and handing
  them to deck.gl as **binary attributes** — one GPU-instanced `ScatterplotLayer`, zero
  per-point JS objects.
- Initial load time is dominated by **embedding transfer + GPU upload**, not by parsing
  (there is nothing to parse — the body *is* a `Float32Array`).
- The honest caveat: **the build/test host is RAM-constrained, so the large-scale
  numbers in this document are design targets and projections, not measured results.**
  The methodology below is exactly how you would verify them.
- Slow paths are known and bounded: backed-CSR per-gene reads (§6.1), `rank_genes_groups`
  markers (§6.2, bounded by `CELLSCOPE_MARKER_REST_CAP`), and single-process
  Leiden/UMAP recompute (§6.3). The frontend has a **stride-sampling downsample
  fallback** (§5) to protect frame rate above a threshold.

---

## 2. Why the architecture should be fast (rationale)

The whole design exists to keep per-cell data **out of JavaScript object land** and
**on the GPU**.

| Technique | Where | Why it matters |
|---|---|---|
| **Binary attributes** | `EmbeddingViewport.tsx`, `binary.ts` (§9.5) | deck.gl reads `Float32Array`/`Uint8Array` straight into GPU buffers. No `{x, y}` objects, no `Array.map`, no GC pressure at 5M rows. |
| **GPU instancing** | deck.gl `ScatterplotLayer` | One draw call instances the same quad N times; cost scales with GPU fill, not with JS work per point. |
| **Raw typed-array wire** | all binary endpoints (§4, §9) | The HTTP body *is* the typed array. `new Float32Array(buf)` is a zero-copy view — no JSON parse, no number boxing. An embedding for 5M cells is `8·n_obs = 40 MB` of bytes, decoded in one allocation-free view. |
| **Interleaved xy** | embedding / recompute (§9.4) | `size:2` matches deck.gl `getPosition` exactly; the buffer feeds the attribute with no repack. |
| **Color computed once into a `Uint8Array(n·3)`** | `useColorBuffer.ts` (§7) | The colormap runs over a flat typed array, not per-point objects; the result is another binary attribute (`getFillColor`, `size:3`). |
| **Server owns the heavy data** | `anndata_service.py` (§8) | The browser never holds the expression matrix; it only ever receives the one column it is coloring by. |
| **Backed/lazy AnnData** | §2, `architecture.md` §7 | `obs`/`var`/`obsm` in RAM, `X` on disk: load + render never touches `X`, so first paint is bounded by embedding transfer, not matrix size. |

The key consequence: **rendering cost is roughly independent of `n_vars`** (genes) and
scales with `n_obs` (cells) mostly through bytes transferred and GPU fill rate, not
through JavaScript.

---

## 3. Methodology — how you WOULD benchmark this

Nothing here requires real data. Use synthetic AnnData so the run is reproducible and
network-free (see `examples/generate_sample.py`).

### 3.1 Datasets

Generate synthetic `.h5ad` at four scales, each with a precomputed `X_umap` in `obsm`
so coloring/selection paths are exercised without recompute:

| Label | `n_obs` | `n_vars` | Embedding bytes (`8·n_obs`) | One color channel (`4·n_obs`) |
|---|---|---|---|---|
| `10k` | 10,000 | 2,000 | 80 KB | 40 KB |
| `100k` | 100,000 | 2,000 | 800 KB | 400 KB |
| `1M` | 1,000,000 | 2,000 | 8 MB | 4 MB |
| `5M` | 5,000,000 | 2,000 | 40 MB | 20 MB |

The payload-size column is *exact*, not a projection: it follows directly from the wire
protocol (§9, [`protocol.md`](./protocol.md) §7) — `8·n_obs` bytes for the interleaved-xy
embedding and `4·n_obs` bytes for one continuous/categorical color channel. These are
the only large transfers in the hot path.

### 3.2 What to measure

1. **Frame rate (FPS).** Measure in the **browser**, not synthetically:
   - Chrome DevTools → Performance/Rendering → "Frame Rendering Stats" (FPS meter), or
     the Performance panel's frames track while panning/zooming.
   - `requestAnimationFrame` delta sampling over a scripted pan/zoom is a reproducible
     alternative. Report **median and 5th-percentile** FPS during continuous interaction
     (the worst case — a sustained pan — is what users feel), not a static idle number.
   - Pin GPU/quality variables: window size, `devicePixelRatio`, point radius, and
     whether downsampling (§5) is active. FPS is meaningless without these.
2. **Initial load time**, split into its two real components:
   - **Embedding transfer**: time to receive `8·n_obs` bytes (network-bound;
     near-instant on localhost, WAN-bound when remote).
   - **GPU upload + first paint**: time from "buffer in hand" to first rendered frame
     (the `Float32Array` → GPU buffer upload).
   - Parsing time is ~0 by design (the body is already a typed array).
3. **Color update latency**: time from gene click to recolor — one
   `4·n_obs`-byte fetch (§4.6) + one `Uint8Array(n·3)` colormap pass + GPU re-upload.
4. **Selection round trip**: client-side point-in-polygon over positions → POST of an
   `Int32Array` (§4.7) → `SelectionRef`. Then **stats latency** separately (it is
   compute-bound, see §6.2).

### 3.3 Reporting rules (so numbers stay honest)

- Always report **hardware** (CPU, GPU, RAM), **browser + version**, window size and
  `devicePixelRatio`, and whether the server was local or remote.
- Separate **transfer** from **GPU upload** from **compute**; a single "load time"
  number hides which one dominates.
- For markers/recompute, report `rest_cells_used` and the `notes` the API returns
  (§6.5) — the result is a *sample*, and the sample size is part of the number.

---

## 4. Design targets (PROJECTIONS — not measured here)

> **These are targets the architecture is designed to meet on a modern desktop GPU
> (e.g. an integrated/discrete GPU from the last few years), serving over localhost.
> They were NOT benchmarked for this build (see §7). Treat them as hypotheses to verify
> with §3, not as results.**

| Scale | Render FPS (interaction) | Embedding transfer (localhost) | GPU upload + first paint | Downsample needed? |
|---|---|---|---|---|
| `10k` | 60 (vsync-capped) | < 1 ms (80 KB) | negligible | no |
| `100k` | 60 (vsync-capped) | a few ms (800 KB) | low | no |
| `1M` | ~60, target ≥ 30 worst case | tens of ms (8 MB) | noticeable but sub-second | optional |
| `5M` | target ≥ 30; ~60 with downsampling | ~40 MB transfer (network-dependent) | the dominant cost; sub-second on a desktop GPU is the target | recommended above the threshold |

Rationale for the shape of this table:

- **10k–100k** should be vsync-capped (60 FPS) because deck.gl GPU instancing makes a
  few hundred thousand instanced points cheap; the bottleneck is fill rate, not point
  count.
- **1M–5M** is where GPU fill rate and the 40 MB upload start to matter. The design
  target is to stay **interactive (≥ 30 FPS)** at 5M, and to reach ~60 FPS by enabling
  the **stride-sampling downsample** (§5), which caps the *rendered* count while keeping
  the full dataset resident for selection/stats.
- **Load time** at every scale is dominated by **embedding transfer + GPU upload**, not
  parsing — there is no parse step. On localhost transfer is trivial up to ~1M; at 5M
  the 40 MB transfer and the GPU upload are the two things to watch, and over a WAN the
  transfer dominates.

---

## 5. The downsampling fallback (what it does and its trade-offs)

When the rendered count would overwhelm the GPU (large `n_obs` on a weak GPU), the
frontend can render a **subset** while keeping the full dataset in memory. This is the
`downsample` / `downsampleN` state in the Zustand store (§7).

### What it does

- **Stride sampling.** Above a threshold, render every *k*-th point so that roughly
  `downsampleN` points are drawn: `k = ceil(n_obs / downsampleN)`, rendered indices
  `0, k, 2k, …`. Stride (deterministic) is chosen over random sampling so the rendered
  set is **stable across frames** (no shimmering as you pan) and trivially cheap to
  compute over a typed array.
- It only changes **what is drawn**. The full `positions` (§7) stay resident, so
  **selection still operates on all cells** — point-in-polygon runs over the complete
  position array, and the POSTed `Int32Array` (§4.7) references full-dataset indices.
  Statistics and recompute therefore use the true cells, not the rendered subset.

### Trade-offs

- **Visual:** sparse regions can look thinner or drop small clusters; density structure
  is approximate. A rare cell type with very few cells may not appear in the rendered
  subset (even though it is still selectable if you lasso its region — but you can't see
  it to lasso it). This is the honest cost of stride sampling.
- **Not a fix for transfer/memory:** the full embedding is still fetched and held;
  downsampling protects **frame rate / GPU fill**, not bandwidth or RAM. Lowering
  `downsampleN` trades fidelity for FPS.
- **Determinism vs. representativeness:** stride sampling is stable but can alias against
  any ordering structure in the file (e.g. cells grouped by sample). If your `.h5ad` is
  ordered, the rendered subset may be biased; randomized sampling would trade stability
  for representativeness. CellScope chooses stability.

---

## 6. Slow paths (documented, bounded, honest)

These are the operations that are **not** GPU-cheap. They are server-side and
compute-bound; the architecture bounds them rather than pretending they are free.

### 6.1 Backed-mode per-gene expression reads (CSR)

Coloring by a gene reads **one column** of `X` for all cells (§4.6, `get_expression`).
In **backed mode** (§2) `X` lives on disk, typically stored **CSR** (compressed sparse
row). CSR is optimized for *row* (per-cell) slicing, so extracting a single **column**
(one gene across all cells) must touch many row blocks — it is the **slow path** of the
hot loop.

- **Symptom:** the *first* color-by-gene on a large backed CSR dataset has visible
  latency (disk + sparse-column gather); subsequent reads of the same gene are faster if
  the OS page cache is warm.
- **Mitigations:**
  - **Convert to CSC** (column-sparse) if you mostly color by gene: column slicing
    becomes contiguous. Trade-off: CSC makes per-cell row slicing (used by markers /
    recompute subsetting) slower — pick the layout that matches your dominant workflow.
  - **Convert to dense** (or raise `CELLSCOPE_BACKED_THRESHOLD_MB` so the file loads
    **in-memory**, `backed=None`): gene reads become a plain in-RAM column view, the
    fastest option — at the cost of holding the whole matrix in RAM.
  - Color by an **obs column** instead where possible: `obs` is always in RAM (§2), so
    obs coloring never hits this path.

### 6.2 `rank_genes_groups` markers

`POST …/selection/stats` (§4.7) runs scanpy `rank_genes_groups` with
`method="wilcoxon"`, comparing the selection against a **random sample of the rest**.

- **Bounded by `CELLSCOPE_MARKER_REST_CAP`** (default 50000, §3). The "rest" group is
  subsampled to at most this many cells so the test does not scale with full `n_obs`.
  The response reports the actual count in `rest_cells_used` and an explicit caveat in
  `notes` (§6.5) — e.g. `"rest subsampled to 50000"`. **The marker result is a sample;
  the sample size is part of the answer.**
- **In backed mode** (§8) this loads `adata[selection].to_memory()` **plus** the sampled
  rest `.to_memory()`, concatenates, labels, and runs the test. Cost scales with
  `n_selected + rest_cells_used` and `n_vars`, not full `n_obs`.
- **Tuning:** lower `CELLSCOPE_MARKER_REST_CAP` for faster, noisier marker tests; raise
  it for more statistical power at higher cost and memory.

### 6.3 Single-process Leiden / UMAP recompute

`recluster` / `recompute_umap` jobs (§5, §8) run in **one worker thread** in a
**single process**. The pipeline is: subset to the selection `.to_memory()`, reuse
existing `X_pca` rows if present (else `sc.pp.pca`), then `sc.pp.neighbors` →
`sc.tl.leiden` (igraph flavor, default fallback) / `sc.tl.umap` (steps `subset`, `pca`,
`neighbors`, `leiden`, `umap`, `finalize` — §5).

- **Not parallel across jobs / not GIL-immune:** heavy numeric work happens under one
  worker; concurrent jobs queue rather than scale across cores. This is acceptable
  because recompute is an explicit, occasional user action on a **subset**, not a
  per-frame cost — and progress is streamed over the WebSocket (§5) so the UI stays
  responsive.
- **Bounded by selection size**, not `n_obs`: you recompute over `n_selected` cells.
  Reusing `X_pca` rows (when present) skips the PCA step entirely.
- **Tuning:** select fewer cells; ensure `X_pca` exists in the source `.h5ad` to skip
  PCA; lower `n_neighbors` / `n_pcs` in the job `params` (§5) for faster, coarser graphs.

---

## 7. What was NOT benchmarked here (and why)

**No large-scale numbers in this document were measured.** Per the project constraints,
the build/test host is **RAM-constrained** and agents do not run installs, builds,
downloads, or compute — **CI runs the builds**, and no GPU benchmarking rig was used.
Concretely:

- The **FPS** figures and the **1M / 5M** load-time figures in §4 are **design targets /
  projections**, derived from the architecture (GPU instancing, binary attributes, no
  per-point JS objects), **not** from a profiler run.
- The **payload-size** numbers in §3.1 (`8·n_obs`, `4·n_obs`) **are** exact — they are
  defined by the wire protocol (§9), not measured, so they hold regardless of hardware.
- The slow-path *characterizations* in §6 are structural (CSR column access, sampling
  caps, single-process compute) and follow from the design and from scanpy/AnnData
  behavior; the specific *timings* are not measured here.

To turn the projections into results, run §3 on real hardware and replace §4 with a
table that includes the hardware/browser/window context required by §3.3.

---

## 8. Tuning cheat-sheet

| Goal | Knob | Effect / trade-off |
|---|---|---|
| Faster gene coloring on big data | Convert `X` to **CSC** | Contiguous column reads; slower per-cell row slicing (markers/recompute). |
| Fastest possible gene coloring | Convert `X` to **dense** or raise `CELLSCOPE_BACKED_THRESHOLD_MB` to force **in-memory** (`backed=None`) | In-RAM column view; costs full-matrix RAM. |
| Hold more datasets fully in RAM | Raise `CELLSCOPE_BACKED_THRESHOLD_MB` | More RAM used, faster `X` reads. Lower it on small hosts to stay lazy. |
| Protect frame rate at 1M–5M | Enable **`downsample`**, set **`downsampleN`** (§7) | Renders ~`downsampleN` points via stride sampling; selection/stats still use all cells. Lower `downsampleN` = more FPS, less fidelity. |
| Faster / cheaper markers | Lower `CELLSCOPE_MARKER_REST_CAP` | Smaller "rest" sample → faster, noisier; reported in `rest_cells_used`/`notes`. |
| More marker power | Raise `CELLSCOPE_MARKER_REST_CAP` | Larger "rest" sample → more power, more time + memory. |
| Faster recompute | Smaller selection; ensure `X_pca` exists; lower `n_neighbors`/`n_pcs` params (§5) | Skips PCA / shrinks the kNN graph; coarser results. |
| More cached selections | Raise `CELLSCOPE_SELECTION_LRU` | More selections kept addressable by `selection_id` (§2). |

The full env-var list lives in **§3** of the contract; the backed/lazy memory model and
where RAM is spent per operation is in [`architecture.md`](./architecture.md) §7.
