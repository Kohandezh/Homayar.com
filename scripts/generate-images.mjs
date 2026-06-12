#!/usr/bin/env node
/**
 * Homayar programmatic artwork generator.
 * Builds abstract, premium enterprise-SaaS artwork as SVG and rasterizes to PNG via sharp.
 * Deterministic: seeded PRNG per slug, so re-runs are byte-stable and every product is distinct.
 *
 *   node scripts/generate-images.mjs
 */
import { existsSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import sharp from 'sharp';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const PRODUCTS_JSON = path.join(ROOT, 'src/data/products.json');

// ---------------------------------------------------------------------------
// Palette
// ---------------------------------------------------------------------------
const P = {
  bg0: '#060a14', bg1: '#0b1322', bg2: '#101a30',
  line: '#1d2a48', ink: '#e8edf8',
};
const ACCENTS = { cyan: '#22d3ee', violet: '#8b5cf6', amber: '#f7a41a', teal: '#2dd4bf' };
const PARTNER = { cyan: 'violet', violet: 'cyan', amber: 'teal', teal: 'violet' };
const ACCENT_KEYS = Object.keys(ACCENTS);

// ---------------------------------------------------------------------------
// Deterministic randomness
// ---------------------------------------------------------------------------
function hashString(str) {
  let h = 1779033703 ^ str.length;
  for (let i = 0; i < str.length; i++) {
    h = Math.imul(h ^ str.charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  h = Math.imul(h ^ (h >>> 16), 2246822507);
  h = Math.imul(h ^ (h >>> 13), 3266489909);
  return (h ^= h >>> 16) >>> 0;
}
function mulberry32(a) {
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rngFor = (seed) => mulberry32(hashString(seed));
const rr = (rng, min, max) => min + rng() * (max - min);
const pick = (rng, arr) => arr[Math.floor(rng() * arr.length)];
const f = (n) => +n.toFixed(1);

// ---------------------------------------------------------------------------
// SVG primitives
// ---------------------------------------------------------------------------
function sceneDefs(W, H, A, B) {
  const orbs = Object.entries(ACCENTS)
    .map(([k, c]) => `<radialGradient id="orb-${k}">
      <stop offset="0" stop-color="#ffffff" stop-opacity="0.85"/>
      <stop offset="0.16" stop-color="${c}" stop-opacity="0.7"/>
      <stop offset="1" stop-color="${c}" stop-opacity="0"/></radialGradient>`)
    .join('');
  return `<defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0.7" y2="1">
      <stop offset="0" stop-color="${P.bg0}"/><stop offset="0.55" stop-color="${P.bg1}"/>
      <stop offset="1" stop-color="${P.bg2}"/></linearGradient>
    <linearGradient id="flow" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="${A}"/><stop offset="1" stop-color="${B}"/></linearGradient>
    <linearGradient id="cardg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${P.bg2}" stop-opacity="0.92"/>
      <stop offset="1" stop-color="${P.bg1}" stop-opacity="0.85"/></linearGradient>
    <radialGradient id="ambA"><stop offset="0" stop-color="${A}" stop-opacity="0.17"/>
      <stop offset="1" stop-color="${A}" stop-opacity="0"/></radialGradient>
    <radialGradient id="ambB"><stop offset="0" stop-color="${B}" stop-opacity="0.11"/>
      <stop offset="1" stop-color="${B}" stop-opacity="0"/></radialGradient>
    <radialGradient id="vig"><stop offset="0.5" stop-color="#020409" stop-opacity="0"/>
      <stop offset="1" stop-color="#020409" stop-opacity="0.72"/></radialGradient>
    <radialGradient id="gridFade"><stop offset="0" stop-color="#ffffff" stop-opacity="0.85"/>
      <stop offset="0.72" stop-color="#ffffff" stop-opacity="0.35"/>
      <stop offset="1" stop-color="#ffffff" stop-opacity="0"/></radialGradient>
    <mask id="gridMask"><rect width="${W}" height="${H}" fill="url(#gridFade)"/></mask>
    <pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse">
      <path d="M32 0H0V32" fill="none" stroke="${P.line}" stroke-opacity="0.55" stroke-width="1"/>
    </pattern>
    <filter id="glow" x="-80%" y="-80%" width="260%" height="260%">
      <feGaussianBlur stdDeviation="9"/></filter>
    <filter id="glowBig" x="-80%" y="-80%" width="260%" height="260%">
      <feGaussianBlur stdDeviation="22"/></filter>
    <filter id="soft" x="-60%" y="-60%" width="220%" height="220%">
      <feGaussianBlur stdDeviation="3"/></filter>
    ${orbs}
  </defs>`;
}

function background(W, H, rng) {
  const ax = f(rr(rng, 0.28, 0.72) * W), ay = f(rr(rng, 0.28, 0.6) * H);
  const bx = f(rr(rng, 0.25, 0.75) * W), by = f(rr(rng, 0.45, 0.8) * H);
  return `<rect width="${W}" height="${H}" fill="url(#bg)"/>
    <ellipse cx="${ax}" cy="${ay}" rx="${f(W * 0.52)}" ry="${f(H * 0.46)}" fill="url(#ambA)"/>
    <ellipse cx="${bx}" cy="${by}" rx="${f(W * 0.42)}" ry="${f(H * 0.38)}" fill="url(#ambB)"/>
    <rect width="${W}" height="${H}" fill="url(#grid)" mask="url(#gridMask)" opacity="0.55"/>`;
}
const vignette = (W, H) => `<rect width="${W}" height="${H}" fill="url(#vig)"/>`;

/** A path drawn twice: blurred underlay for glow, crisp stroke on top. */
function glowPath(d, stroke, w, op = 0.95) {
  return `<path d="${d}" fill="none" stroke="${stroke}" stroke-width="${f(w * 3.2)}"
      stroke-opacity="${f(op * 0.4)}" stroke-linecap="round" filter="url(#glow)"/>
    <path d="${d}" fill="none" stroke="${stroke}" stroke-width="${w}"
      stroke-opacity="${op}" stroke-linecap="round"/>`;
}

/** Glowing node: soft halo + bright core. `key` is an accent name. */
function node(x, y, r, key, halo = 3.4) {
  return `<circle cx="${f(x)}" cy="${f(y)}" r="${f(r * halo)}" fill="url(#orb-${key})"/>
    <circle cx="${f(x)}" cy="${f(y)}" r="${f(r)}" fill="${ACCENTS[key]}"/>
    <circle cx="${f(x)}" cy="${f(y)}" r="${f(r * 0.45)}" fill="#ffffff" fill-opacity="0.85"/>`;
}

/** Dashboard chrome: frosted rounded card with hairline border + top highlight. */
function card(x, y, w, h, r = 16) {
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="url(#cardg)"
      stroke="${P.line}" stroke-opacity="0.95"/>
    <path d="M ${x + r} ${y + 1} H ${x + w - r}" stroke="${P.ink}" stroke-opacity="0.09"/>`;
}

/** Skeleton text rows inside a card (pure geometry, no fonts). */
function skeleton(rng, x, y, w, rows, gap = 22) {
  let s = '';
  for (let i = 0; i < rows; i++) {
    const rw = f(w * rr(rng, 0.45, 0.95));
    s += `<rect x="${x}" y="${f(y + i * gap)}" width="${rw}" height="7" rx="3.5"
      fill="${P.ink}" fill-opacity="${i === 0 ? 0.22 : 0.1}"/>`;
  }
  return s;
}

/** Rising bar chart inside a box; tallest bar gets a glowing cap. */
function barChart(rng, x, y, w, h, n, key, key2) {
  const hex = ACCENTS[key]; const gap = w / n; const bw = f(gap * 0.52);
  let s = ''; let best = [0, 0, 0];
  for (let i = 0; i < n; i++) {
    const t = (i + 1) / n;
    const bh = f(h * (0.22 + 0.78 * (t * 0.62 + rng() * 0.38)));
    const bx = f(x + i * gap + (gap - bw) / 2); const by = f(y + h - bh);
    const k = i === n - 2 && key2 ? ACCENTS[key2] : hex;
    s += `<rect x="${bx}" y="${by}" width="${bw}" height="${bh}" rx="${f(bw / 2)}"
      fill="${k}" fill-opacity="${f(0.14 + t * 0.32)}"/>`;
    if (bh > best[2]) best = [bx + bw / 2, by, bh];
  }
  return s + node(best[0], best[1], 4, key);
}

/** Catmull-Rom smoothing -> cubic bezier path through points. */
function smoothPath(pts) {
  let d = `M ${f(pts[0][0])} ${f(pts[0][1])}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(i - 1, 0)], p1 = pts[i], p2 = pts[i + 1];
    const p3 = pts[Math.min(i + 2, pts.length - 1)];
    d += ` C ${f(p1[0] + (p2[0] - p0[0]) / 6)} ${f(p1[1] + (p2[1] - p0[1]) / 6)},`
      + ` ${f(p2[0] - (p3[0] - p1[0]) / 6)} ${f(p2[1] - (p3[1] - p1[1]) / 6)},`
      + ` ${f(p2[0])} ${f(p2[1])}`;
  }
  return d;
}

/** Tapered oscillating wave points across [x0,x1]. */
function wavePoints(rng, x0, x1, cy, amp, n = 22, taper = 0.7) {
  const ph1 = rng() * 6.28, ph2 = rng() * 6.28;
  const f1 = rr(rng, 1.4, 2.4), f2 = rr(rng, 3.2, 5);
  const pts = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    const env = Math.sin(Math.PI * t) ** taper;
    const y = cy + env * (Math.sin(t * f1 * 6.28 + ph1) * amp * 0.72
      + Math.sin(t * f2 * 6.28 + ph2) * amp * 0.28);
    pts.push([x0 + (x1 - x0) * t, y]);
  }
  return pts;
}

/** Noisy rising sparkline path inside a box. */
function sparkline(rng, x, y, w, h, key, weight = 2.5) {
  const pts = [];
  const n = 8;
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    pts.push([x + w * t, y + h - h * (0.18 + 0.64 * t + rr(rng, -0.16, 0.16))]);
  }
  const last = pts[n];
  return glowPath(smoothPath(pts), ACCENTS[key], weight, 0.9) + node(last[0], last[1], 3.5, key);
}

function arcPath(cx, cy, r, a0, a1) {
  const pt = (a) => [cx + r * Math.cos((a * Math.PI) / 180), cy + r * Math.sin((a * Math.PI) / 180)];
  const [x0, y0] = pt(a0), [x1, y1] = pt(a1);
  return `M ${f(x0)} ${f(y0)} A ${f(r)} ${f(r)} 0 ${Math.abs(a1 - a0) > 180 ? 1 : 0} 1 ${f(x1)} ${f(y1)}`;
}
const arcPoint = (cx, cy, r, a) =>
  [cx + r * Math.cos((a * Math.PI) / 180), cy + r * Math.sin((a * Math.PI) / 180)];

/** Row of small status dots. */
function dotRow(x, y, key, n = 4, gap = 18) {
  let s = '';
  for (let i = 0; i < n; i++) {
    const on = i === n - 1;
    s += `<circle cx="${x + i * gap}" cy="${y}" r="4" fill="${on ? ACCENTS[key] : P.ink}"
      fill-opacity="${on ? 0.9 : 0.16}"/>`;
  }
  return s;
}

// ---------------------------------------------------------------------------
// Motif renderers — each returns SVG content for a W x H scene.
// Signature: (rng, a, b, W, H) where a/b are accent keys (primary/partner).
// ---------------------------------------------------------------------------

function mWaveform(rng, a, b, W, H) {
  const cy = H * 0.46 + rr(rng, -30, 30);
  const amp = H * 0.15;
  const main = wavePoints(rng, W * 0.06, W * 0.94, cy, amp, 26);
  let s = `<path d="M ${f(W * 0.06)} ${f(cy)} H ${f(W * 0.94)}" stroke="${P.line}" stroke-opacity="0.9"/>`;
  s += glowPath(smoothPath(wavePoints(rng, W * 0.09, W * 0.91, cy + 14, amp * 0.6, 24)), ACCENTS[b], 2.2, 0.45);
  s += glowPath(smoothPath(wavePoints(rng, W * 0.09, W * 0.91, cy - 10, amp * 0.75, 24)), ACCENTS[a], 2.2, 0.4);
  s += glowPath(smoothPath(main), 'url(#flow)', 5, 1);
  for (let i = 0; i < 3; i++) {
    const p = main[6 + Math.floor(rng() * 15)];
    s += node(p[0], p[1], 4, i === 1 ? 'amber' : a);
  }
  const cx = W * 0.6, cyy = H * 0.74;
  s += card(cx, cyy, 290, 150) + sparkline(rng, cx + 30, cyy + 38, 230, 84, a);
  s += card(W * 0.1, H * 0.13, 230, 92) + skeleton(rng, W * 0.1 + 26, H * 0.13 + 30, 178, 2, 26);
  s += dotRow(W * 0.1 + 26, H * 0.13 + 92 - 22, b);
  return s;
}

function mGauge(rng, a, b, W, H) {
  const cx = W * 0.5, cy = H * 0.48, R = H * 0.26;
  const a0 = 135, sweep = 270, frac = rr(rng, 0.6, 0.84);
  let s = `<circle cx="${cx}" cy="${cy}" r="${f(R * 1.24)}" fill="none" stroke="${P.line}" stroke-opacity="0.7"/>`;
  for (let i = 0; i <= 27; i++) {
    const ang = a0 + (sweep / 27) * i; const major = i % 3 === 0;
    const [x1, y1] = arcPoint(cx, cy, R * (major ? 1.1 : 1.07), ang);
    const [x2, y2] = arcPoint(cx, cy, R * 1.16, ang);
    s += `<path d="M ${f(x1)} ${f(y1)} L ${f(x2)} ${f(y2)}" stroke="${P.ink}"
      stroke-opacity="${major ? 0.3 : 0.12}" stroke-width="${major ? 2 : 1}"/>`;
  }
  s += `<path d="${arcPath(cx, cy, R, a0, a0 + sweep)}" fill="none" stroke="${P.line}"
    stroke-width="14" stroke-linecap="round"/>`;
  s += glowPath(arcPath(cx, cy, R, a0, a0 + sweep * frac), 'url(#flow)', 14, 0.95);
  s += `<path d="${arcPath(cx, cy, R * 0.78, a0, a0 + sweep * rr(rng, 0.3, 0.55))}" fill="none"
    stroke="${ACCENTS[b]}" stroke-opacity="0.4" stroke-width="5" stroke-linecap="round"/>`;
  const [ex, ey] = arcPoint(cx, cy, R, a0 + sweep * frac);
  s += node(ex, ey, 7, a);
  s += `<circle cx="${cx}" cy="${cy}" r="5" fill="${P.ink}" fill-opacity="0.3"/>`;
  s += `<rect x="${cx - 60}" y="${f(cy + R * 0.45)}" width="120" height="12" rx="6" fill="${P.ink}" fill-opacity="0.12"/>`;
  const px = W * 0.09, py = H * 0.76;
  s += card(px, py, 270, 140) + barChart(rng, px + 28, py + 28, 214, 84, a, b);
  s += dotRow(W * 0.74, H * 0.14, a);
  return s;
}

function mScorecard(rng, a, b, W, H) {
  const x = W * 0.115, y = H * 0.16, gw = W * 0.77;
  const big = { w: gw * 0.58, h: 300 };
  let s = card(x, y, big.w, big.h, 20);
  s += skeleton(rng, x + 34, y + 36, big.w * 0.5, 1);
  s += sparkline(rng, x + 34, y + 92, big.w - 68, big.h - 142, a, 3.2);
  const sx = x + big.w + 28;
  for (let i = 0; i < 2; i++) {
    const sy = y + i * 158;
    s += card(sx, sy, gw - big.w - 28, 142);
    s += skeleton(rng, sx + 26, sy + 32, gw - big.w - 100, 1);
    s += `<rect x="${sx + 26}" y="${sy + 72}" width="${f((gw - big.w - 80) * rr(rng, 0.45, 0.9))}"
      height="10" rx="5" fill="${ACCENTS[i ? b : a]}" fill-opacity="0.6"/>`;
    s += `<rect x="${sx + 26}" y="${sy + 72}" width="${gw - big.w - 80}" height="10" rx="5"
      fill="${P.ink}" fill-opacity="0.07"/>`;
    s += dotRow(sx + 26, sy + 112, i ? b : a, 3);
  }
  const by = y + big.h + 28;
  s += card(x, by, gw, 190, 20) + barChart(rng, x + 36, by + 34, gw - 72, 122, a, b);
  return s;
}

function mGraph(rng, a, b, W, H) {
  const pts = [];
  for (let gy = 0; gy < 3; gy++) {
    for (let gx = 0; gx < 4; gx++) {
      if (rng() < 0.18) continue;
      pts.push([
        W * 0.16 + gx * W * 0.225 + rr(rng, -50, 50),
        H * 0.2 + gy * H * 0.27 + rr(rng, -45, 45),
      ]);
    }
  }
  const cx = W / 2, cy = H / 2;
  let hub = 0, hd = 1e9;
  pts.forEach((p, i) => {
    const d = (p[0] - cx) ** 2 + (p[1] - cy) ** 2;
    if (d < hd) { hd = d; hub = i; }
  });
  let s = '', hi = '';
  pts.forEach((p, i) => pts.forEach((q, j) => {
    if (j <= i) return;
    const d = Math.hypot(p[0] - q[0], p[1] - q[1]);
    if (d > W * 0.3) return;
    const line = `M ${f(p[0])} ${f(p[1])} L ${f(q[0])} ${f(q[1])}`;
    if (i === hub || j === hub) hi += glowPath(line, ACCENTS[a], 2, 0.55);
    else s += `<path d="${line}" stroke="${P.line}" stroke-opacity="0.9" stroke-width="1.4"/>`;
  }));
  s += hi;
  pts.forEach((p, i) => {
    if (i === hub) s += node(p[0], p[1], 10, a, 4.5);
    else if (rng() < 0.3) s += node(p[0], p[1], 5, b, 2.8);
    else s += `<circle cx="${f(p[0])}" cy="${f(p[1])}" r="5" fill="${P.bg2}"
      stroke="${P.ink}" stroke-opacity="0.4"/>`;
  });
  s += card(W * 0.66, H * 0.8, 250, 110) + skeleton(rng, W * 0.66 + 26, H * 0.8 + 30, 198, 2, 26);
  s += dotRow(W * 0.66 + 26, H * 0.8 + 86, a, 3);
  return s;
}

function mFunnel(rng, a, b, W, H) {
  const cx = W / 2, top = H * 0.14, bandH = 96, gap = 30;
  const widths = [0.66, 0.5, 0.36, 0.24].map((w) => w * rr(rng, 0.92, 1.08));
  let s = '';
  widths.forEach((w, i) => {
    const w2 = widths[i + 1] ?? w * 0.66;
    const y = top + i * (bandH + gap);
    const d = `M ${f(cx - W * w / 2)} ${y} L ${f(cx + W * w / 2)} ${y}
      L ${f(cx + W * w2 / 2)} ${y + bandH} L ${f(cx - W * w2 / 2)} ${y + bandH} Z`;
    const hex = i === widths.length - 1 ? ACCENTS[b] : ACCENTS[a];
    s += `<path d="${d}" fill="${hex}" fill-opacity="${f(0.07 + i * 0.07)}"
      stroke="${hex}" stroke-opacity="${f(0.3 + i * 0.16)}" stroke-width="1.6"/>`;
    s += glowPath(`M ${f(cx - W * w / 2)} ${y} L ${f(cx + W * w / 2)} ${y}`, hex, 2.4, 0.5 + i * 0.12);
    if (i < widths.length - 1) {
      s += `<path d="M ${cx} ${y + bandH + 4} L ${cx} ${y + bandH + gap - 4}"
        stroke="${P.ink}" stroke-opacity="0.25" stroke-width="2" stroke-dasharray="2 6"/>`;
    }
  });
  const ly = top + widths.length * (bandH + gap) + 14;
  s += node(cx, ly, 8, 'amber');
  s += card(W * 0.66, H * 0.13, 240, 104) + skeleton(rng, W * 0.66 + 26, H * 0.13 + 30, 188, 2, 26);
  s += dotRow(W * 0.12, H * 0.85, a, 4);
  return s;
}

function mHeatmap(rng, a, b, W, H) {
  const n = 7, cell = 76, gapC = 14;
  const size = n * cell + (n - 1) * gapC;
  const x0 = (W - size) / 2, y0 = (H - size) / 2 - 20;
  const hx = rr(rng, 1, n - 2), hy = rr(rng, 1, n - 2);
  let s = '', hot = [];
  for (let r = 0; r < n; r++) {
    for (let c = 0; c < n; c++) {
      const dist = Math.hypot(c - hx, r - hy) / n;
      const v = Math.max(0, Math.min(1, 1.05 - dist * 1.9 + rr(rng, -0.22, 0.22)));
      const x = x0 + c * (cell + gapC), y = y0 + r * (cell + gapC);
      const hex = v > 0.4 && rng() < 0.16 ? ACCENTS[b] : ACCENTS[a];
      s += `<rect x="${f(x)}" y="${f(y)}" width="${cell}" height="${cell}" rx="14"
        fill="${hex}" fill-opacity="${f(0.05 + v * 0.5)}"
        stroke="${P.line}" stroke-opacity="0.8"/>`;
      if (v > 0.82) hot.push([x, y, hex]);
    }
  }
  hot.slice(0, 3).forEach(([x, y, hex]) => {
    s += `<rect x="${f(x)}" y="${f(y)}" width="${cell}" height="${cell}" rx="14"
      fill="${hex}" fill-opacity="0.5" filter="url(#glow)"/>
      <rect x="${f(x)}" y="${f(y)}" width="${cell}" height="${cell}" rx="14"
      fill="none" stroke="${hex}" stroke-opacity="0.95" stroke-width="2"/>`;
  });
  s += dotRow(x0, y0 + size + 46, a, 5);
  s += `<rect x="${f(x0 + size - 180)}" y="${f(y0 + size + 38)}" width="180" height="9" rx="4.5"
    fill="${P.ink}" fill-opacity="0.1"/>`;
  return s;
}

function mStack(rng, a, b, W, H) {
  const cx = W / 2, hw = W * 0.24, hh = hw * 0.42;
  const layers = 3, dy = hh * 1.55, topY = H * 0.32;
  const dia = (y) => `M ${f(cx - hw)} ${f(y)} L ${cx} ${f(y - hh)} L ${f(cx + hw)} ${f(y)} L ${cx} ${f(y + hh)} Z`;
  let s = '';
  for (let i = layers - 1; i >= 0; i--) {
    const y = topY + i * dy;
    const hex = i === 0 ? ACCENTS[a] : i === 1 ? ACCENTS[b] : P.ink;
    if (i < layers - 1) {
      s += `<path d="M ${f(cx - hw)} ${f(y)} V ${f(y + dy * 0.55)} M ${f(cx + hw)} ${f(y)}
        V ${f(y + dy * 0.55)} M ${cx} ${f(y + hh)} V ${f(y + hh + dy * 0.55)}"
        stroke="${P.line}" stroke-width="1.5"/>`;
    }
    s += `<path d="${dia(y)}" fill="${P.bg2}" fill-opacity="0.88"
      stroke="${hex}" stroke-opacity="${i === 0 ? 0 : 0.45}" stroke-width="1.6"/>`;
    if (i === 0) s += glowPath(dia(y), ACCENTS[a], 2.6, 0.95);
    s += `<path d="${dia(y)}" fill="url(#orb-${i === 0 ? a : b})" fill-opacity="${i === 0 ? 0.35 : 0.12}"/>`;
  }
  s += node(cx, topY - hh, 7, a);
  s += `<path d="M ${cx} ${f(topY - hh - 18)} V ${f(H * 0.12)}" stroke="${ACCENTS[a]}"
    stroke-opacity="0.5" stroke-width="1.6" stroke-dasharray="2 7"/>`;
  s += node(cx, H * 0.12, 4.5, 'amber', 2.6);
  const px = W * 0.64, py = H * 0.76;
  s += card(px, py, 270, 130) + skeleton(rng, px + 26, py + 32, 218, 2, 26) + dotRow(px + 26, py + 104, b, 3);
  return s;
}

function mShield(rng, a, b, W, H) {
  const cx = W / 2, top = H * 0.18, w = W * 0.23, h = H * 0.52;
  const shield = (sw, sh, y0) => `M ${f(cx - sw)} ${f(y0)}
    C ${f(cx - sw * 0.45)} ${f(y0 - sh * 0.045)} ${f(cx + sw * 0.45)} ${f(y0 - sh * 0.045)} ${f(cx + sw)} ${f(y0)}
    V ${f(y0 + sh * 0.5)} C ${f(cx + sw)} ${f(y0 + sh * 0.78)} ${f(cx + sw * 0.55)} ${f(y0 + sh * 0.92)} ${cx} ${f(y0 + sh)}
    C ${f(cx - sw * 0.55)} ${f(y0 + sh * 0.92)} ${f(cx - sw)} ${f(y0 + sh * 0.78)} ${f(cx - sw)} ${f(y0 + sh * 0.5)} Z`;
  let lattice = '';
  for (let i = -14; i < 14; i++) {
    lattice += `<path d="M ${cx + i * 36} ${top - 40} l 420 760" stroke="${ACCENTS[a]}" stroke-opacity="0.13"/>
      <path d="M ${cx + i * 36} ${top - 40} l -420 760" stroke="${ACCENTS[b]}" stroke-opacity="0.1"/>`;
  }
  let s = `<clipPath id="shieldClip"><path d="${shield(w, h, top)}"/></clipPath>
    <path d="${shield(w, h, top)}" fill="${P.bg2}" fill-opacity="0.85"/>
    <g clip-path="url(#shieldClip)">${lattice}</g>`;
  s += glowPath(shield(w, h, top), ACCENTS[a], 3, 0.95);
  s += `<path d="${shield(w * 0.7, h * 0.7, top + h * 0.15)}" fill="none"
    stroke="${ACCENTS[b]}" stroke-opacity="0.4" stroke-width="1.6"/>`;
  s += node(cx, top + h * 0.46, 9, a, 4);
  s += `<circle cx="${cx}" cy="${f(top + h * 0.46)}" r="44" fill="none" stroke="${ACCENTS[a]}"
    stroke-opacity="0.35" stroke-width="1.4" stroke-dasharray="3 8"/>`;
  const px = W * 0.085, py = H * 0.78;
  s += card(px, py, 260, 124) + skeleton(rng, px + 26, py + 32, 208, 2, 26) + dotRow(px + 26, py + 98, a, 4);
  s += dotRow(W * 0.74, H * 0.12, b, 3);
  return s;
}

function mStaircase(rng, a, b, W, H) {
  const n = 5, x0 = W * 0.12, y0 = H * 0.78, stepW = (W * 0.62) / n;
  let s = '';
  const tops = [];
  for (let i = 0; i < n; i++) {
    const sh = 60 + i * rr(rng, 64, 76);
    const x = x0 + i * (stepW + 10), y = y0 - sh;
    tops.push([x + stepW / 2, y]);
    const hex = i === n - 1 ? ACCENTS[a] : P.ink;
    s += `<rect x="${f(x)}" y="${f(y)}" width="${f(stepW)}" height="${f(sh)}" rx="14"
      fill="${i === n - 1 ? ACCENTS[a] : P.bg2}" fill-opacity="${i === n - 1 ? 0.16 : 0.75}"
      stroke="${hex}" stroke-opacity="${i === n - 1 ? 0.8 : 0.2}" stroke-width="1.5"/>`;
    s += `<rect x="${f(x + 18)}" y="${f(y + 16)}" width="${f(stepW - 36)}" height="7" rx="3.5"
      fill="${P.ink}" fill-opacity="0.12"/>`;
  }
  const arcPts = tops.map(([x, y], i) => [x, y - 60 - i * 26 - Math.sin((i / (n - 1)) * Math.PI) * 22]);
  s += glowPath(smoothPath(arcPts), 'url(#flow)', 4.5, 0.95);
  arcPts.forEach(([x, y], i) => {
    if (i === n - 1) s += node(x, y, 7, b);
    else if (i % 2 === 0) s += node(x, y, 3.5, a, 2.6);
  });
  s += node(tops[n - 1][0], tops[n - 1][1] + 30, 4.5, 'amber', 2.8);
  s += card(W * 0.66, H * 0.14, 250, 116) + sparkline(rng, W * 0.66 + 26, H * 0.14 + 26, 198, 66, a, 2);
  s += dotRow(x0, H * 0.88, b, 4);
  return s;
}

function mConnectors(rng, a, b, W, H) {
  const hubX = W / 2, hubY = H * 0.48, sq = 78;
  let s = '';
  const port = (x, y, side, key, lit) => {
    const hex = lit ? ACCENTS[key] : P.line;
    const px = side > 0 ? x + sq : x, py = y + sq / 2;
    const d = `M ${f(px)} ${f(py)} C ${f(px + side * 130)} ${f(py)}, ${f(hubX - side * 170)} ${f(hubY)}, ${f(hubX)} ${f(hubY)}`;
    let g = lit ? glowPath(d, ACCENTS[key], 2.4, 0.8)
      : `<path d="${d}" fill="none" stroke="${P.line}" stroke-width="1.6"/>`;
    g += `<rect x="${f(x)}" y="${f(y)}" width="${sq}" height="${sq}" rx="18" fill="url(#cardg)"
      stroke="${hex}" stroke-opacity="${lit ? 0.9 : 1}" stroke-width="1.6"/>
      <circle cx="${f(x + sq / 2)}" cy="${f(y + sq / 2)}" r="11" fill="none"
        stroke="${lit ? ACCENTS[key] : P.ink}" stroke-opacity="${lit ? 0.9 : 0.3}" stroke-width="2.5"/>
      <circle cx="${f(px)}" cy="${f(py)}" r="4" fill="${hex}"/>`;
    return g;
  };
  for (let i = 0; i < 3; i++) {
    const y = H * 0.22 + i * H * 0.21;
    s += port(W * 0.13, y + rr(rng, -14, 14), 1, i === 1 ? a : b, i === 1);
    s += port(W * 0.87 - sq, y + rr(rng, -14, 14), -1, i === 2 ? b : a, i !== 1);
  }
  s += `<circle cx="${hubX}" cy="${f(hubY)}" r="56" fill="${P.bg2}" stroke="${P.line}"/>`;
  s += `<circle cx="${hubX}" cy="${f(hubY)}" r="56" fill="url(#orb-${a})" fill-opacity="0.5"/>`;
  s += glowPath(arcPath(hubX, hubY, 56, -60, 200), ACCENTS[a], 2.6, 0.9);
  s += node(hubX, hubY, 9, a, 3);
  s += card(W * 0.36, H * 0.8, 280, 104) + skeleton(rng, W * 0.36 + 28, H * 0.8 + 30, 224, 2, 26);
  s += dotRow(W * 0.36 + 28, H * 0.8 + 82, 'amber', 3);
  return s;
}

function mSentiment(rng, a, b, W, H) {
  const y = H * 0.24, h = H * 0.42, w = W * 0.34, gap = W * 0.04;
  const lx = W / 2 - gap / 2 - w, rx = W / 2 + gap / 2;
  let s = card(lx, y, w, h, 20) + card(rx, y, w, h, 20);
  const down = [];
  for (let i = 0; i <= 7; i++) {
    down.push([lx + 34 + ((w - 68) * i) / 7, y + 60 + (h - 130) * (0.25 + 0.6 * (i / 7) + rr(rng, -0.1, 0.1))]);
  }
  s += `<path d="${smoothPath(down)}" fill="none" stroke="${ACCENTS[b]}" stroke-opacity="0.55"
    stroke-width="2.5" stroke-linecap="round"/>`;
  s += `<circle cx="${f(down[7][0])}" cy="${f(down[7][1])}" r="4" fill="${ACCENTS[b]}" fill-opacity="0.7"/>`;
  s += sparkline(rng, rx + 34, y + 56, w - 68, h - 124, a, 3);
  s += skeleton(rng, lx + 34, y + 30, 90, 1) + skeleton(rng, rx + 34, y + 30, 90, 1);
  s += dotRow(lx + 34, y + h - 28, b, 3) + dotRow(rx + 34, y + h - 28, a, 3);
  s += glowPath(`M ${W / 2} ${f(y - 40)} V ${f(y + h + 40)}`, 'url(#flow)', 2, 0.5);
  s += node(W / 2, y - 40, 5, a, 2.8) + node(W / 2, y + h + 40, 5, b, 2.8);
  const by = H * 0.74;
  s += card(lx, by, w * 2 + gap, 140, 20) + barChart(rng, lx + 34, by + 30, w * 2 + gap - 68, 84, a, b);
  return s;
}

function mBubbles(rng, a, b, W, H) {
  const cx = W * 0.52, cy = H * 0.46;
  const spec = [
    [0, 0, 130, a, true], [-225, -130, 78, b, false], [205, -160, 92, a, false],
    [-265, 115, 60, a, false], [255, 150, 70, b, false], [-60, -265, 48, b, false],
    [95, 255, 42, a, false],
  ];
  let s = '';
  spec.forEach(([dx, dy, r, key]) => {
    const x = cx + dx + rr(rng, -18, 18), y = cy + dy + rr(rng, -18, 18);
    if (Math.hypot(dx, dy) > 1) {
      s += `<path d="M ${f(cx)} ${f(cy)} L ${f(x)} ${f(y)}" stroke="${P.line}" stroke-width="1.4"/>`;
    }
    s += `<circle cx="${f(x)}" cy="${f(y)}" r="${r}" fill="url(#orb-${key})" fill-opacity="0.42"/>
      <circle cx="${f(x)}" cy="${f(y)}" r="${r}" fill="none" stroke="${ACCENTS[key]}"
        stroke-opacity="0.55" stroke-width="1.6"/>`;
  });
  s += glowPath(`M ${f(cx + 130)} ${f(cy)} A 130 130 0 1 1 ${f(cx)} ${f(cy - 130)}`, ACCENTS[a], 2.6, 0.9);
  s += node(cx, cy - 130, 6, a);
  for (let i = 0; i < 5; i++) {
    s += `<circle cx="${f(rr(rng, W * 0.12, W * 0.88))}" cy="${f(rr(rng, H * 0.12, H * 0.85))}"
      r="${f(rr(rng, 2, 4))}" fill="${ACCENTS[i % 2 ? b : 'amber']}" fill-opacity="0.5"/>`;
  }
  s += card(W * 0.09, H * 0.79, 250, 116) + skeleton(rng, W * 0.09 + 26, H * 0.79 + 32, 198, 2, 26);
  s += dotRow(W * 0.09 + 26, H * 0.79 + 92, b, 4);
  return s;
}

// ---------------------------------------------------------------------------
// Motif registry + selection
// ---------------------------------------------------------------------------
const MOTIFS = [
  ['waveform', /wave|ribbon|audio|voice|signal/, mWaveform],
  ['gauge', /gauge|dial|meter|score(?!card)/, mGauge],
  ['scorecard', /scorecard|kpi|dashboard|card grid|metric/, mScorecard],
  ['graph', /graph|network|node|knowledge|mesh/, mGraph],
  ['funnel', /funnel|pipeline|conversion|stage/, mFunnel],
  ['heatmap', /heatmap|calendar|matrix|density/, mHeatmap],
  ['stack', /stack|layer|architecture|platform|tier/, mStack],
  ['shield', /shield|security|compliance|privacy|lattice|guard/, mShield],
  ['staircase', /coach|staircase|step|growth|ladder|progress/, mStaircase],
  ['connectors', /connector|plug|integrat|api|webhook|sync/, mConnectors],
  ['sentiment', /sentiment|emotion|split|polarity|mood/, mSentiment],
  ['bubbles', /topic|cluster|bubble|theme|cloud/, mBubbles],
];

function chooseMotif(concept, slug) {
  const c = (concept || '').toLowerCase();
  for (const [name, re, fn] of MOTIFS) if (re.test(c)) return [name, fn];
  const [name, , fn] = MOTIFS[hashString(`motif:${slug}`) % MOTIFS.length];
  return [name, fn];
}
function chooseAccent(concept, slug) {
  const m = (concept || '').toLowerCase().match(/cyan|violet|amber|teal/);
  return m ? m[0] : ACCENT_KEYS[hashString(`accent:${slug}`) % ACCENT_KEYS.length];
}

// ---------------------------------------------------------------------------
// Hero + OG compositions
// ---------------------------------------------------------------------------
function ribbonToAnalytics(rng, x0, x1, cy, amp, scale = 1) {
  // Luminous multi-strand ribbon flowing right, resolving into bars/cards/nodes.
  const split = x0 + (x1 - x0) * 0.56;
  let s = '';
  for (const [off, ampF, wgt, op] of [[-26, 0.85, 2.2, 0.4], [20, 0.6, 2.2, 0.45], [-8, 1.1, 2.6, 0.3]]) {
    s += glowPath(smoothPath(wavePoints(rng, x0, split + 60, cy + off * scale, amp * ampF, 26, 0.55)),
      off > 0 ? ACCENTS.violet : ACCENTS.cyan, wgt * scale, op);
  }
  const main = wavePoints(rng, x0, split + 30, cy, amp, 28, 0.5);
  s += glowPath(smoothPath(main), 'url(#flow)', 5.5 * scale, 1);
  for (const i of [8, 14, 21]) s += node(main[i][0], main[i][1], 4 * scale, i === 14 ? 'amber' : 'cyan');
  // Analytics zone: rising bars on a baseline.
  const bx = split + 40 * scale, bw = (x1 - bx) * 0.55;
  s += `<path d="M ${f(bx)} ${f(cy + amp * 1.15)} H ${f(x1)}" stroke="${P.line}"/>`;
  s += barChart(rng, bx, cy - amp * 0.7, bw, amp * 1.8, 7, 'violet', 'amber');
  // Cards + nodes above and below the bars.
  const cw = 230 * scale, ch = 118 * scale;
  s += card(bx + bw + 36 * scale, cy - amp * 1.25, cw, ch, 16);
  s += sparkline(rng, bx + bw + 36 * scale + 24, cy - amp * 1.25 + 26, cw - 48, ch - 52, 'cyan', 2);
  s += card(bx + bw + 70 * scale, cy + amp * 0.25, cw * 0.86, ch * 0.8, 14);
  s += skeleton(rng, bx + bw + 70 * scale + 22, cy + amp * 0.25 + 26, cw * 0.86 - 44, 2, 22 * scale);
  s += dotRow(bx + bw + 70 * scale + 22, cy + amp * 0.25 + ch * 0.8 - 20, 'amber', 3, 16);
  const n1 = [bx + bw * 0.5, cy - amp * 1.3], n2 = [bx + bw + 36 * scale + cw * 0.5, cy + amp * 1.05];
  s += `<path d="M ${f(n1[0])} ${f(n1[1])} L ${f(n2[0] - 120)} ${f(n2[1] - 50)}" stroke="${P.line}" stroke-width="1.4"/>`;
  s += node(n1[0], n1[1], 5, 'violet', 3) + node(n2[0] - 120, n2[1] - 50, 4, 'teal', 3);
  return s;
}

function heroSVG() {
  const W = 1536, H = 1024, rng = rngFor('homayar-hero-v3');
  let body = `<rect width="${W}" height="${H}" fill="url(#bg)"/>
    <ellipse cx="${W * 0.22}" cy="${H * 0.42}" rx="${W * 0.34}" ry="${H * 0.5}" fill="url(#ambA)"/>
    <ellipse cx="${W * 0.8}" cy="${H * 0.52}" rx="${W * 0.32}" ry="${H * 0.46}" fill="url(#ambB)"/>
    <rect width="${W}" height="${H}" fill="url(#grid)" mask="url(#gridMask)" opacity="0.55"/>`;
  body += ribbonToAnalytics(rng, -60, W - 90, H * 0.5, H * 0.17, 1);
  body += dotRow(W * 0.07, H * 0.12, 'cyan', 4);
  body += `<circle cx="${W * 0.9}" cy="${H * 0.16}" r="3.5" fill="${ACCENTS.amber}" fill-opacity="0.7"/>
    <circle cx="${W * 0.13}" cy="${H * 0.86}" r="3" fill="${ACCENTS.teal}" fill-opacity="0.6"/>`;
  body += vignette(W, H);
  return wrap(W, H, ACCENTS.cyan, ACCENTS.violet, body);
}

function ogSVG() {
  const W = 1200, H = 630, rng = rngFor('homayar-og-v3');
  let body = `<rect width="${W}" height="${H}" fill="url(#bg)"/>
    <ellipse cx="${W * 0.78}" cy="${H * 0.5}" rx="${W * 0.4}" ry="${H * 0.62}" fill="url(#ambA)"/>
    <ellipse cx="${W * 0.55}" cy="${H * 0.75}" rx="${W * 0.3}" ry="${H * 0.4}" fill="url(#ambB)"/>
    <rect width="${W}" height="${H}" fill="url(#grid)" mask="url(#gridMask)" opacity="0.45"/>`;
  // Left third stays calm for overlay text; artwork lives at x > 430.
  body += ribbonToAnalytics(rng, 430, W - 60, H * 0.5, H * 0.16, 0.72);
  body += `<circle cx="${W * 0.38}" cy="${H * 0.16}" r="3" fill="${ACCENTS.amber}" fill-opacity="0.6"/>`;
  body += vignette(W, H);
  return wrap(W, H, ACCENTS.cyan, ACCENTS.violet, body);
}

// ---------------------------------------------------------------------------
// Assembly + rasterization
// ---------------------------------------------------------------------------
function wrap(W, H, A, B, body) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
    ${sceneDefs(W, H, A, B)}${body}</svg>`;
}

function productSVG(product) {
  const W = 1024, H = 1024;
  const rng = rngFor(`homayar:${product.slug}`);
  const [motifName, motif] = chooseMotif(product.imageConcept, product.slug);
  const a = chooseAccent(product.imageConcept, product.slug);
  const b = PARTNER[a];
  const body = background(W, H, rng) + motif(rng, a, b, W, H) + vignette(W, H);
  return { svg: wrap(W, H, ACCENTS[a], ACCENTS[b], body), motifName, accent: a };
}

async function rasterize(svg, outPath, W, H) {
  // Render at 2x density then downsample for clean antialiasing.
  const buf = await sharp(Buffer.from(svg), { density: 144 })
    .resize(W, H)
    .png({ compressionLevel: 9 })
    .toBuffer();
  await mkdir(path.dirname(outPath), { recursive: true });
  await writeFile(outPath, buf);
  return outPath;
}

const SAMPLE_PRODUCTS = [
  { slug: 'sample-call-scoring', name: 'Call Scoring', category: 'Quality', imageConcept: 'radial gauge motif with cyan accent' },
  { slug: 'sample-agent-coaching', name: 'Agent Coaching', category: 'Enablement', imageConcept: 'coaching staircase motif with amber accent' },
  { slug: 'sample-integrations-hub', name: 'Integrations Hub', category: 'Platform', imageConcept: 'connector plug lattice with teal accent' },
  { slug: 'sample-topic-intelligence', name: 'Topic Intelligence', category: 'Analytics', imageConcept: 'topic cluster bubbles with violet accent' },
  { slug: 'dev-waveform', name: 'x', category: 'x', imageConcept: 'voice waveform ribbon with cyan accent' },
  { slug: 'dev-scorecard', name: 'x', category: 'x', imageConcept: 'kpi scorecard grid with violet accent' },
  { slug: 'dev-graph', name: 'x', category: 'x', imageConcept: 'knowledge graph network with teal accent' },
  { slug: 'dev-funnel', name: 'x', category: 'x', imageConcept: 'conversion funnel with cyan accent' },
  { slug: 'dev-heatmap', name: 'x', category: 'x', imageConcept: 'heatmap calendar with violet accent' },
  { slug: 'dev-stack', name: 'x', category: 'x', imageConcept: 'layered architecture stack with cyan accent' },
  { slug: 'dev-shield', name: 'x', category: 'x', imageConcept: 'security shield lattice with teal accent' },
  { slug: 'dev-sentiment', name: 'x', category: 'x', imageConcept: 'sentiment split panel with amber accent' },
];

async function main() {
  let products, outDir, mode;
  if (existsSync(PRODUCTS_JSON)) {
    products = JSON.parse(await readFile(PRODUCTS_JSON, 'utf8'));
    outDir = path.join(ROOT, 'public/products');
    mode = 'products';
  } else {
    products = SAMPLE_PRODUCTS;
    outDir = path.join(ROOT, 'public/products-test');
    mode = 'test (src/data/products.json not found)';
  }
  console.log(`mode: ${mode} — ${products.length} product image(s)`);

  for (const product of products) {
    const { svg, motifName, accent } = productSVG(product);
    const out = await rasterize(svg, path.join(outDir, `${product.slug}.png`), 1024, 1024);
    console.log(`  ${path.relative(ROOT, out)}  [${motifName}/${accent}]`);
  }

  await rasterize(heroSVG(), path.join(ROOT, 'public/hero.png'), 1536, 1024);
  console.log('  public/hero.png  [hero ribbon]');
  await rasterize(ogSVG(), path.join(ROOT, 'public/og.png'), 1200, 630);
  console.log('  public/og.png  [og card]');
}

main().catch((err) => { console.error(err); process.exit(1); });
