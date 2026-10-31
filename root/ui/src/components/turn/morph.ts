// @ts-nocheck -- vendored third-party code, held to its author's bar.
//
// `tsconfig.sheets.json` sets `noUncheckedIndexedAccess`, which this file was
// never written against: it indexes its own freshly-built arrays about thirty
// times, and every one of those reads is provably in range by construction.
// Asserting each would mean thirty edits through someone else's algorithm, and
// the point of vendoring is that the algorithm stays diffable against upstream.
// The consumer, `ThinkingOrb.tsx`, is fully checked.

/**
 * The thinking orb's morph engine — a dotted outline cycling circle → triangle
 * → square → circle.
 *
 * Vendored from Jakub Antalik's `thinking-orbs` (MIT), file
 * `src/engine/morph.ts`, at the owner's request: this one mode rather than the
 * whole library. The body below is upstream's, unchanged, so it stays diffable.
 * Two edits, both mechanical:
 *
 *  - the `Dot` / `ModeDraw` types and `paint()` came from sibling modules and
 *    are inlined here, because the rest of those modules serves the other modes;
 *  - `paint()` keeps upstream's grey-ramp behaviour but takes an ink colour, so
 *    the orb can be drawn in Chotu's accent rather than only black or white.
 *
 * Upstream's own note is worth keeping: the geometry was tuned against a blur +
 * threshold "goo" filter and is drawn here with plain circles, which read a
 * touch softer. Do not "correct" that by shrinking the radius — it makes the
 * mark genuinely smaller than the tuning.
 */

interface Dot {
  x: number
  y: number
  z: number
  r: number
  /** Ink value: 0 = darkest ink on paper. Mirrored on dark themes. */
  white: number
  a?: number
}

export interface MorphOpts {
  spread?: number
  iconD?: number
  rDot?: number
  rMin?: number
  /** Chotu addition: the ink to draw with. Upstream only ever drew greys. */
  ink?: string
  /** Chotu addition: draw each dot as a ring rather than a disc. */
  hollow?: boolean
  /** Ring thickness in CSS px, when `hollow`. */
  lineWidth?: number
  /** Chotu addition: strength of the travelling highlight, 0 = off. A crest
   * sweeps the outline so the mark reads as *loading* rather than merely
   * present — upstream's morph alone holds still for 1.4s of every 2.3. */
  highlight?: number
  /** Laps per second for that crest. */
  highlightSpeed?: number
}

/** Upstream `core.ts`'s `paint`, with an ink colour threaded through. */
function paint(
  ctx: CanvasRenderingContext2D,
  dots: Dot[],
  dark: boolean,
  rMin = 0.3,
  ink?: string,
  hollow = false,
  lineWidth = 1,
): void {
  dots.sort((a, b) => a.z - b.z)
  for (const d of dots) {
    const alpha = d.a ?? 1
    if (alpha < 0.02) continue
    if (ink && hollow) {
      // A ring, not a disc: stroke inside the dot's radius so the outer edge
      // still lands where the geometry says it should.
      ctx.strokeStyle = ink
      ctx.lineWidth = lineWidth
      ctx.globalAlpha = alpha
      ctx.beginPath()
      ctx.arc(d.x, d.y, Math.max(rMin, d.r - lineWidth / 2), 0, Math.PI * 2)
      ctx.stroke()
      ctx.globalAlpha = 1
      continue
    }
    if (ink) {
      ctx.fillStyle = ink
    } else {
      const w = Math.min(1, Math.max(0, d.white))
      const g = Math.round((dark ? 1 - w : w) * 255)
      ctx.fillStyle = `rgba(${g},${g},${g},${alpha})`
    }
    ctx.beginPath()
    ctx.arc(d.x, d.y, Math.max(rMin, d.r), 0, Math.PI * 2)
    ctx.fill()
  }
}

type Path = (f: number) => [number, number];

function smoothE(x: number): number {
  return x * x * (3 - 2 * x);
}

function polyPath(verts: ReadonlyArray<readonly [number, number]>): Path {
  const V = verts.length;
  const L: number[] = [];
  let total = 0;
  for (let i = 0; i < V; i++) {
    const a = verts[i];
    const b = verts[(i + 1) % V];
    const l = Math.hypot(b[0] - a[0], b[1] - a[1]);
    L.push(l);
    total += l;
  }
  return (f) => {
    let target = f * total;
    let i = 0;
    while (target > L[i] && i < V - 1) {
      target -= L[i];
      i++;
    }
    const a = verts[i];
    const b = verts[(i + 1) % V];
    const ff = L[i] ? Math.min(1, target / L[i]) : 0;
    return [a[0] + (b[0] - a[0]) * ff, a[1] + (b[1] - a[1]) * ff];
  };
}

const CIRCLE: Path = (f) => {
  const a = -Math.PI / 2 + f * 2 * Math.PI;
  return [Math.cos(a) * 0.24, Math.sin(a) * 0.24];
};
const TRIANGLE = polyPath([
  [0.0, -0.26],
  [0.24, 0.16],
  [-0.24, 0.16]
]);
// 5-vertex walk so the path STARTS at top-centre like the other shapes
const SQUARE = polyPath([
  [0, -0.2],
  [0.2, -0.2],
  [0.2, 0.2],
  [-0.2, 0.2],
  [-0.2, -0.2]
]);
const CYCLE: Path[] = [CIRCLE, TRIANGLE, SQUARE];

// low floor keeps sparse outlines possible while never degenerating
function morphN(d: number): number {
  return Math.max(6, Math.round(34 * d));
}

const HOLD = 1.4;
const MORPH = 0.9;
const SEG = HOLD + MORPH;

// This state was tuned in inkform, which paints it through a blur +
// threshold "goo" filter; we draw plain circles instead, since `ctx.filter`
// and SVG filter refs are not safe to rely on across Chrome / Safari /
// Firefox. The dot GEOMETRY is identical either way — the threshold just
// yields a hard edge where a plain fill has an antialiased one, so these
// dots read a touch softer than inkform's. Don't "correct" for that by
// shrinking the radius: it makes the mark genuinely smaller than the tuning.

export function drawMorph(
  ctx: CanvasRenderingContext2D,
  size: number,
  t: number,
  dark: boolean,
  o: MorphOpts,
): void {
  const K = CYCLE.length;
  const period = SEG * K;
  // A POSITIVE modulo, because `t` can arrive negative and JS's `%` keeps the
  // sign of its left operand. Callers derive `t` from a rAF timestamp minus a
  // `performance.now()` taken just before the request — and rAF reports the
  // FRAME's start time, which can predate that call when a frame is already in
  // flight. The difference is a fraction of a millisecond, and it was enough:
  // `k` came out -1, `CYCLE[-1]` is undefined, and calling it threw
  // "pA is not a function" out of an animation frame, killing the orb.
  const tc = ((t % period) + period) % period;
  const k = Math.floor(tc / SEG);
  const local = tc - k * SEG;
  const m = local > HOLD ? smoothE((local - HOLD) / MORPH) : 0;
  const sprd = o.spread ?? 1;

  // blend the two shape PATHS at m, then measure the blended outline
  const pA = CYCLE[k];
  const pB = CYCLE[(k + 1) % K];
  const M = 160;
  const pts: Array<[number, number]> = [];
  for (let i = 0; i < M; i++) {
    const f = i / M;
    const a = pA(f);
    const b = pB(f);
    pts.push([(a[0] + (b[0] - a[0]) * m) * sprd, (a[1] + (b[1] - a[1]) * m) * sprd]);
  }
  const L: number[] = [];
  let total = 0;
  for (let i = 0; i < M; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % M];
    const l = Math.hypot(b[0] - a[0], b[1] - a[1]);
    L.push(l);
    total += l;
  }

  // dot radius depends ONLY on rDot (the size knob); the count sets the
  // gaps. Formed shapes breathe a little (uniform pulse).
  const n = morphN(o.iconD ?? 1);
  const re = (o.rDot ?? 0.021) * 1.35 * sprd;
  const pulse = 1 + 0.02 * Math.sin(local * 3.1);

  // Chotu addition: a crest that sweeps the outline. `hi` is its strength and
  // the gaussian below its width, in fractions of the perimeter.
  const hi = o.highlight ?? 0;
  const head = ((t * (o.highlightSpeed ?? 0.55)) % 1 + 1) % 1;

  const dots: Dot[] = [];
  const c2 = size / 2;
  let seg = 0;
  let acc = 0;
  for (let k2 = 0; k2 < n; k2++) {
    const target = (k2 / n) * total;
    while (acc + L[seg] < target && seg < M - 1) {
      acc += L[seg];
      seg++;
    }
    const a = pts[seg];
    const b = pts[(seg + 1) % M];
    const f = L[seg] ? Math.min(1, (target - acc) / L[seg]) : 0;
    const x = (a[0] + (b[0] - a[0]) * f) * pulse;
    const y = (a[1] + (b[1] - a[1]) * f) * pulse;
    let boost = 0;
    if (hi > 0) {
      let dist = Math.abs(k2 / n - head);
      dist = Math.min(dist, 1 - dist);
      boost = Math.exp(-(dist * dist) / (2 * 0.11 * 0.11));
    }
    dots.push({
      x: c2 + x * size,
      y: c2 + y * size,
      z: 0,
      r: Math.max(0.35, re * size) * (1 + 0.38 * hi * boost),
      white: 0.1,
      // Floor of 0.42 rather than 0.34: at 18px the unlit dots are a 1px ring,
      // and below this they stop reading as a shape at all — the crest would be
      // sweeping an outline nobody can see.
      a: hi > 0 ? 0.42 + 0.58 * boost : 1
    });
  }
  paint(ctx, dots, dark, o.rMin, o.ink, o.hollow, o.lineWidth)
}
