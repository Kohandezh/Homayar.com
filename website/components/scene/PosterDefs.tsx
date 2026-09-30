function mulberry(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const W = 1920;
const H = 1080;
const CX = W / 2;
const CY = H / 2;
const f = (n: number) => Math.round(n * 10) / 10;

function corridor(rows: number, withLongLines: boolean) {
  const parts: string[] = [];
  const horizon = CY + 60;
  if (withLongLines) {
    for (let i = -14; i <= 14; i++) {
      parts.push(`M${f(CX + i * 190)} ${H}L${f(CX + i * 6)} ${horizon}`);
    }
  }
  for (let k = 0; k < rows; k++) {
    const y = horizon + (H - horizon) * Math.pow((k + 1) / rows, 2.2);
    parts.push(`M0 ${f(y)}L${W} ${f(y)}`);
  }
  return parts.join("");
}

function wave() {
  const pts: string[] = [];
  const n = 120;
  for (let i = 0; i <= n; i++) {
    const x = CX - 540 + (1080 * i) / n;
    const u = (i / n) * Math.PI * 2;
    const env = Math.exp(-Math.pow((i / n - 0.5) * 2.6, 2));
    const y = CY + (Math.sin(u * 2.3) * 70 + Math.sin(u * 5.1) * 28 + Math.sin(u * 9.7) * 10) * env;
    pts.push(`${i === 0 ? "M" : "L"}${f(x)} ${f(y)}`);
  }
  return pts.join("");
}

function brain() {
  const rnd = mulberry(11);
  const N = 150;
  const pts: Array<[number, number, number]> = [];
  for (let i = 0; i < N; i++) {
    const a = rnd() * Math.PI * 2;
    const r = 0.35 + 0.65 * Math.sqrt(rnd());
    const x = CX + Math.cos(a) * r * 560 + (rnd() - 0.5) * 60;
    const y = CY + Math.sin(a) * r * 330 + (rnd() - 0.5) * 60;
    pts.push([x, y, 2 + rnd() * 3.5]);
  }
  const links: string[] = [];
  for (let i = 0; i < N; i++) {
    let made = 0;
    for (let j = i + 1; j < N && made < 2; j++) {
      const dx = pts[i][0] - pts[j][0];
      const dy = pts[i][1] - pts[j][1];
      if (dx * dx + dy * dy < 130 * 130) {
        links.push(`M${f(pts[i][0])} ${f(pts[i][1])}L${f(pts[j][0])} ${f(pts[j][1])}`);
        made++;
      }
    }
  }
  return { pts, links: links.join("") };
}

function field() {
  const parts: string[] = [];
  const horizon = CY + 40;
  for (let i = -10; i <= 10; i++) {
    parts.push(`M${f(CX + i * 96)} ${H}L${f(CX + i * 18)} ${horizon}`);
  }
  for (let k = 0; k < 9; k++) {
    const y = horizon + (H - horizon) * Math.pow(k / 9, 2.1);
    parts.push(`M0 ${f(y)}L${W} ${f(y)}`);
  }
  return parts.join("");
}

const br = brain();

function dots() {
  const rnd = mulberry(21);
  const out: Array<[number, number, number]> = [];
  for (let i = 0; i < 420; i++) {
    const a = rnd() * Math.PI * 2;
    const r = Math.sqrt(rnd());
    out.push([CX + Math.cos(a) * r * 300, CY + Math.sin(a) * r * 300, 2 + (1 - r) * 3]);
  }
  return out;
}
const sphereDots = dots();

export function PosterDefs() {
  return (
    <svg width="0" height="0" style={{ position: "absolute", width: 0, height: 0 }} aria-hidden="true" focusable="false">
      <defs>
        <radialGradient id="poster-fog" cx="50%" cy="50%" r="55%">
          <stop offset="0" stopColor="#fff" stopOpacity="0" />
          <stop offset="0.45" stopColor="#fff" stopOpacity="0.55" />
          <stop offset="1" stopColor="#fff" stopOpacity="1" />
        </radialGradient>
        <radialGradient id="poster-glow" cx="50%" cy="50%" r="50%">
          <stop offset="0" stopColor="currentColor" stopOpacity="0.85" />
          <stop offset="0.5" stopColor="currentColor" stopOpacity="0.25" />
          <stop offset="1" stopColor="currentColor" stopOpacity="0" />
        </radialGradient>
        <mask id="poster-fog-mask">
          <rect width={W} height={H} fill="url(#poster-fog)" />
        </mask>
        <symbol id="poster-tunnel" viewBox={`0 0 ${W} ${H}`}>
          <g mask="url(#poster-fog-mask)" fill="none" stroke="currentColor" strokeWidth="2">
            <path d={corridor(16, true)} opacity="0.55" />
          </g>
          <path d={wave()} fill="none" stroke="currentColor" strokeWidth="4" strokeLinecap="round" opacity="0.9" />
        </symbol>
        <symbol id="poster-warp" viewBox={`0 0 ${W} ${H}`}>
          <g mask="url(#poster-fog-mask)" fill="none" stroke="currentColor" strokeWidth="2">
            <path d={corridor(24, true)} opacity="0.7" />
          </g>
          <rect x={CX - 90} y={CY - 90} width="180" height="180" fill="url(#poster-glow)" />
          <rect x={CX - 60} y={CY - 60} width="120" height="120" fill="none" stroke="currentColor" strokeWidth="4" opacity="0.95" />
        </symbol>
        <symbol id="poster-brain" viewBox={`0 0 ${W} ${H}`}>
          <path d={br.links} fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.45" />
          <g fill="currentColor" opacity="0.9">
            {br.pts.map((p, i) => (
              <circle key={i} cx={f(p[0])} cy={f(p[1])} r={f(p[2])} />
            ))}
          </g>
          <circle cx={CX} cy={CY} r="160" fill="url(#poster-glow)" />
          <circle cx={CX} cy={CY} r="22" fill="currentColor" />
        </symbol>
        <symbol id="poster-particles" viewBox={`0 0 ${W} ${H}`}>
          <circle cx={CX} cy={CY} r="340" fill="url(#poster-glow)" opacity="0.5" />
          <g fill="currentColor" opacity="0.85">
            {sphereDots.map((p, i) => (
              <circle key={i} cx={f(p[0])} cy={f(p[1])} r={f(p[2])} />
            ))}
          </g>
        </symbol>
        <symbol id="poster-field" viewBox={`0 0 ${W} ${H}`}>
          <path d={field()} fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.4" />
        </symbol>
      </defs>
    </svg>
  );
}
