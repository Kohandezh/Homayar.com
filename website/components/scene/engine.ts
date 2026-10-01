import { AddEquation, BufferAttribute, BufferGeometry, Color, ColorManagement, CustomBlending, Float32BufferAttribute, Group, Line, LineLoop, LineSegments, Material, Mesh, OneFactor, OneMinusSrcAlphaFactor, PerspectiveCamera, PlaneGeometry, Points, Scene, ShaderMaterial, Timer, Vector3, WebGLRenderer } from "three";
import { ScrollTrigger } from "@/lib/gsap";
import { sceneBus, type SceneKind } from "./bus";
import { ENTER_END, focusAt, REPORT_IN, REPORT_PEAK } from "./timing";

type Theme = "dark" | "light";

const PALETTE: Record<Theme, { line: string; bright: string; deep: string }> = {
  dark: { line: "#22bec6", bright: "#7fdde2", deep: "#125a5e" },
  light: { line: "#125a5e", bright: "#0d4346", deep: "#1a8a90" },
};

function applyBlend(m: Material, theme: Theme) {
  m.blending = CustomBlending;
  m.blendEquation = AddEquation;
  m.blendSrc = OneFactor;
  m.blendDst = theme === "light" ? OneMinusSrcAlphaFactor : OneFactor;
  m.blendSrcAlpha = OneFactor;
  m.blendDstAlpha = OneMinusSrcAlphaFactor;
  m.needsUpdate = true;
}

type Role = "line" | "bright" | "deep";

const LINE_VERT = `
uniform float uNear; uniform float uFar;
attribute vec3 aColor; attribute float aShade;
varying float vFade; varying vec3 vColor;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vFade = (1.0 - smoothstep(uNear, uFar, -mv.z)) * aShade;
  vColor = aColor;
  gl_Position = projectionMatrix * mv;
}`;
const LINE_FRAG = `
precision mediump float;
uniform vec3 uColor; uniform float uOpacity; uniform float uUseVertex;
varying float vFade; varying vec3 vColor;
void main() {
  vec3 c = mix(uColor, vColor, uUseVertex);
  float a = uOpacity * vFade;
  gl_FragColor = vec4(c * a, a);
}`;
const POINT_VERT = `
uniform float uNear; uniform float uFar; uniform float uSize; uniform float uDpr;
attribute float aSize; attribute vec3 aColor;
varying float vFade; varying vec3 vColor;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vFade = 1.0 - smoothstep(uNear, uFar, -mv.z);
  vColor = aColor;
  gl_PointSize = uSize * aSize * uDpr * min(3.5, 24.0 / max(1.0, -mv.z));
  gl_Position = projectionMatrix * mv;
}`;
const POINT_FRAG = `
precision mediump float;
uniform vec3 uColor; uniform float uOpacity; uniform float uUseVertex;
varying float vFade; varying vec3 vColor;
void main() {
  vec2 c = gl_PointCoord - 0.5;
  float d = length(c);
  float a = smoothstep(0.5, 0.1, d);
  vec3 col = mix(uColor, vColor, uUseVertex);
  float al = a * uOpacity * vFade;
  gl_FragColor = vec4(col * al, al);
}`;

type Mat = ShaderMaterial & { userData: { role: Role } };

function makeMat(kind: "line" | "point", role: Role, theme: Theme, opts: { near: number; far: number; opacity: number; size?: number; useVertex?: boolean; dpr?: number }): Mat {
  const pal = PALETTE[theme];
  const m = new ShaderMaterial({
    vertexShader: kind === "line" ? LINE_VERT : POINT_VERT,
    fragmentShader: kind === "line" ? LINE_FRAG : POINT_FRAG,
    uniforms: {
      uColor: { value: new Color(pal[role]) },
      uOpacity: { value: opts.opacity },
      uNear: { value: opts.near },
      uFar: { value: opts.far },
      uSize: { value: opts.size ?? 2 },
      uDpr: { value: opts.dpr ?? 1 },
      uUseVertex: { value: opts.useVertex ? 1 : 0 },
    },
    transparent: true,
    depthWrite: false,
    depthTest: false,
  }) as Mat;
  applyBlend(m, theme);
  m.userData.role = role;
  return m;
}

function geo(positions: number[], colors?: number[], shades?: number[]) {
  const g = new BufferGeometry();
  g.setAttribute("position", new Float32BufferAttribute(positions, 3));
  g.setAttribute("aColor", new Float32BufferAttribute(colors ?? new Array(positions.length).fill(1), 3));
  g.setAttribute("aShade", new Float32BufferAttribute(shades ?? new Array(positions.length / 3).fill(1), 1));
  return g;
}

function dim(ctx: Ctx) {
  return ctx.theme === "light" ? 0.55 : 1;
}

function smooth(a: number, b: number, x: number) {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
}

interface Unit {
  kind: SceneKind;
  scene: Scene;
  camera: PerspectiveCamera;
  mats: Mat[];
  update(p: number, t: number, alpha: number): void;
  dispose(): void;
}

interface Ctx {
  theme: Theme;
  light: boolean;
  rtl: boolean;
  dpr: number;
  pointer: { x: number; y: number };
  smoothPointer: { x: number; y: number };
}

const TUNNEL_DIST = 90;

function makeTunnel(ctx: Ctx, warp: boolean): Unit {
  const scene = new Scene();
  const camera = new PerspectiveCamera(64, 1, 0.1, 160);
  const W = 8;
  const H = 4.5;
  const L = 132;
  const step = 3;
  const period = 24;
  const mats: Mat[] = [];

  const FW = 30;
  const pos: number[] = [];
  const xs = ctx.light ? 2 : 1;
  for (let x = -FW; x <= FW + 0.001; x += xs) pos.push(x, -H, 4, x, -H, -L);
  const longMat = makeMat("line", "deep", ctx.theme, { near: 6, far: 84, opacity: 0.6 });
  const long = new LineSegments(geo(pos), longMat);

  const rp: number[] = [];
  const rs: number[] = [];
  for (let z = 4; z >= -L; z -= step) {
    const idx = Math.round((4 - z) / step);
    const gate = idx % (period / step) === 0;
    rp.push(-FW, -H, z, FW, -H, z);
    const shade = gate ? 1 : 0.4 + 0.2 * (idx % 3);
    rs.push(shade, shade);
    if (gate) {
      rp.push(-FW, -H + 0.04, z - 0.35, FW, -H + 0.04, z - 0.35);
      rs.push(0.9, 0.9);
    }
  }
  const ringMat = makeMat("line", "line", ctx.theme, { near: 4, far: 72, opacity: 0.85 });
  const rings = new LineSegments(geo(rp, undefined, rs), ringMat);
  const group = new Group();
  group.add(long, rings);
  mats.push(longMat, ringMat);

  const SN = ctx.light ? 24 : 60;
  const rnd = mulberry(5);
  const streakBase: number[] = [];
  for (let i = 0; i < SN; i++) {
    const x = (rnd() - 0.5) * 2 * (W + 2);
    const y = -H + 0.1 + rnd() * 2.2;
    streakBase.push(x, y, -rnd() * 74);
  }
  const streakPos = new Float32Array(SN * 6);
  const streakGeo = new BufferGeometry();
  streakGeo.setAttribute("position", new BufferAttribute(streakPos, 3));
  streakGeo.setAttribute("aColor", new Float32BufferAttribute(new Array(SN * 6).fill(1), 3));
  streakGeo.setAttribute("aShade", new Float32BufferAttribute(new Array(SN * 2).fill(1), 1));
  const streakMat = makeMat("line", "bright", ctx.theme, { near: 2, far: 74, opacity: 0 });
  const streaks = new LineSegments(streakGeo, streakMat);
  mats.push(streakMat);

  const N = ctx.light ? 90 : 180;
  const wavePos = new Float32Array(N * 3);
  const waveGeo = new BufferGeometry();
  waveGeo.setAttribute("position", new BufferAttribute(wavePos, 3));
  waveGeo.setAttribute("aColor", new Float32BufferAttribute(new Array(N * 3).fill(1), 3));
  waveGeo.setAttribute("aShade", new Float32BufferAttribute(new Array(N).fill(1), 1));
  waveGeo.setAttribute("aSize", new Float32BufferAttribute(new Array(N).fill(1), 1));
  const wavePtMat = makeMat("point", "bright", ctx.theme, { near: 2, far: 60, opacity: 0.95, size: 2.4, dpr: ctx.dpr });
  const waveLnMat = makeMat("line", "bright", ctx.theme, { near: 2, far: 60, opacity: 0.8 });
  const wavePts = new Points(waveGeo, wavePtMat);
  const waveLine = new Line(waveGeo, waveLnMat);
  mats.push(wavePtMat, waveLnMat);

  const squareMat = new ShaderMaterial({
    vertexShader: "varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
    fragmentShader:
      "precision mediump float; uniform vec3 uColor; uniform float uOpacity; varying vec2 vUv; void main(){ vec2 c = vUv - 0.5; float d = max(abs(c.x), abs(c.y)) * 2.0; float glow = pow(1.0 - smoothstep(0.0, 1.0, d), 2.2); float edge = smoothstep(0.86, 0.9, d) * (1.0 - smoothstep(0.9, 0.96, d)); float al = (glow * 0.85 + edge * 0.9) * uOpacity; gl_FragColor = vec4(uColor * al, al); }",
    uniforms: { uColor: { value: new Color(ctx.theme === "light" ? PALETTE.light.line : PALETTE.dark.bright) }, uOpacity: { value: 0 } },
    transparent: true,
    depthWrite: false,
    depthTest: false,
  });
  applyBlend(squareMat, ctx.theme);
  const square = new Mesh(new PlaneGeometry(2.6, 2.6), squareMat);
  square.position.z = -70;
  const frameMat = makeMat("line", "bright", ctx.theme, { near: 2, far: 90, opacity: 0.9 });
  const frame = new LineLoop(geo([-1.3, -1.3, 0, 1.3, -1.3, 0, 1.3, 1.3, 0, -1.3, 1.3, 0]), frameMat);
  square.add(frame);
  mats.push(frameMat);
  scene.add(group, streaks, wavePts, waveLine, square);

  const cam = { x: 0, y: 0, rx: 0, ry: 0 };
  let lastP = -1;
  let lastT = 0;
  let speed = 0;

  return {
    kind: warp ? "warp" : "tunnel",
    scene,
    camera,
    mats,
    update(p, t, alpha) {
      const sp = ctx.smoothPointer;
      const travel = warp ? 60 * p + 180 * p * p : TUNNEL_DIST * p;
      const dt = Math.max(0.001, t - lastT);
      if (lastP >= 0) {
        const target = Math.min(1, Math.abs(p - lastP) / dt / (warp ? 0.9 : 0.55));
        speed += (target - speed) * (target > speed ? 0.35 : 0.06);
      }
      lastP = p;
      lastT = t;
      group.position.z = ((travel % period) + period) % period;
      group.scale.z = warp ? 1 - 0.6 * smooth(0.2, 0.9, p) : 1;
      cam.x += (sp.x * 1.6 - cam.x) * 0.08;
      cam.y += (sp.y * 1.0 - cam.y) * 0.08;
      cam.ry += (-sp.x * 0.18 - cam.ry) * 0.08;
      cam.rx += (sp.y * 0.12 - cam.rx) * 0.08;
      camera.position.set(cam.x, cam.y - 0.6, 0);
      camera.rotation.set(cam.rx - 0.06, cam.ry, 0);

      const len = 0.5 + speed * 7;
      for (let i = 0; i < SN; i++) {
        const z0 = streakBase[i * 3 + 2] + travel;
        const zw = ((((z0 + 70) % 74) + 74) % 74) - 70;
        const x = streakBase[i * 3];
        const y = streakBase[i * 3 + 1];
        streakPos[i * 6] = x;
        streakPos[i * 6 + 1] = y;
        streakPos[i * 6 + 2] = zw;
        streakPos[i * 6 + 3] = x;
        streakPos[i * 6 + 4] = y;
        streakPos[i * 6 + 5] = zw + len;
      }
      streakGeo.attributes.position.needsUpdate = true;
      streakMat.uniforms.uOpacity.value = smooth(0.08, 0.7, speed) * 0.9 * alpha;

      const env = 0.55 + 0.45 * Math.sin(t * 0.7);
      const px = sp.x * 6;
      const fadeWave = warp ? 1 - smooth(0.15, 0.55, p) : 1;
      for (let i = 0; i < N; i++) {
        const x = -5.5 + (11 * i) / (N - 1);
        const boost = 1 + 2.2 * Math.exp(-((x - px) * (x - px)) / 2.2);
        const y = (Math.sin(x * 1.7 + t * 1.8) * 0.6 * env + Math.sin(x * 4.3 - t * 2.6) * 0.28 + Math.sin(x * 9.1 + t * 4.1) * 0.1) * boost;
        wavePos[i * 3] = x;
        wavePos[i * 3 + 1] = y - sp.y * 0.6 - (ctx.light ? 1.9 : 0);
        wavePos[i * 3 + 2] = -16 - (warp ? 30 * smooth(0.2, 0.9, p) : 0);
      }
      waveGeo.attributes.position.needsUpdate = true;
      wavePtMat.uniforms.uOpacity.value = 0.95 * fadeWave * alpha;
      waveLnMat.uniforms.uOpacity.value = 0.8 * fadeWave * alpha;
      longMat.uniforms.uOpacity.value = 0.6 * alpha * dim(ctx);
      ringMat.uniforms.uOpacity.value = (0.85 + 0.15 * speed) * alpha * dim(ctx);
      const sq = warp ? smooth(0.25, 0.95, p) : 0;
      square.position.z = -70 + 68 * sq;
      const s = 1 + 6 * sq * sq;
      square.scale.set(s, s, 1);
      squareMat.uniforms.uOpacity.value = (warp ? smooth(0.3, 0.85, p) : 0) * alpha;
      frameMat.uniforms.uOpacity.value = (warp ? 0.3 + 0.7 * sq : 0) * alpha;
    },
    dispose() {
      scene.traverse((o) => {
        const m = o as Mesh;
        m.geometry?.dispose();
      });
      mats.forEach((m) => m.dispose());
      squareMat.dispose();
    },
  };
}

function makeBrain(ctx: Ctx): Unit {
  const scene = new Scene();
  const camera = new PerspectiveCamera(58, 1, 0.1, 80);
  const N = ctx.light ? 520 : 1500;
  const mats: Mat[] = [];
  const pts: number[] = [];
  const base: number[] = [];
  const sizes: number[] = [];
  const rnd = mulberry(7);
  const R = { x: 5.4, y: 4.1, z: 4.6 };
  const anchors: Vector3[] = [];
  for (let i = 0; i < 5; i++) {
    const side = i % 2 === 0 ? 1 : -1;
    anchors.push(new Vector3(side * 3.3, 2.2 - i * 1.05, 1.2 - i * 0.5));
  }
  const group = new Array<number>(N).fill(-1);
  for (let i = 0; i < N; i++) {
    const u = rnd() * 2 - 1;
    const phi = rnd() * Math.PI * 2;
    const r = 0.5 + 0.5 * Math.pow(rnd(), 0.35);
    const sx = Math.sqrt(1 - u * u);
    let x = r * sx * Math.cos(phi) * R.x;
    const y = r * u * R.y + Math.sin(phi * 3) * 0.25;
    const z = r * sx * Math.sin(phi) * R.z;
    x += Math.sign(x) * 0.35;
    pts.push(x, y, z);
    const shade = 0.55 + 0.45 * rnd();
    base.push(shade, shade, shade);
    sizes.push(0.6 + rnd() * 1.1);
    const p = new Vector3(x, y, z);
    anchors.forEach((a, k) => {
      if (p.distanceTo(a) < 1.7) group[i] = k;
    });
  }
  const colorArr = new Float32Array(base);
  const pal = PALETTE[ctx.theme];
  const cLine = new Color(pal.line);
  const cBright = new Color(pal.bright);
  for (let i = 0; i < N; i++) {
    colorArr[i * 3] = cLine.r * base[i * 3];
    colorArr[i * 3 + 1] = cLine.g * base[i * 3 + 1];
    colorArr[i * 3 + 2] = cLine.b * base[i * 3 + 2];
  }
  const g = new BufferGeometry();
  g.setAttribute("position", new Float32BufferAttribute(pts, 3));
  g.setAttribute("aColor", new BufferAttribute(colorArr, 3));
  g.setAttribute("aShade", new Float32BufferAttribute(new Array(N).fill(1), 1));
  g.setAttribute("aSize", new Float32BufferAttribute(sizes, 1));
  const ptMat = makeMat("point", "line", ctx.theme, { near: 1, far: 22, opacity: 0.9, size: 1.7, dpr: ctx.dpr, useVertex: true });
  const points = new Points(g, ptMat);
  mats.push(ptMat);

  const cell = 1.15;
  const grid = new Map<string, number[]>();
  const key = (x: number, y: number, z: number) => `${Math.floor(x / cell)},${Math.floor(y / cell)},${Math.floor(z / cell)}`;
  for (let i = 0; i < N; i++) {
    const k = key(pts[i * 3], pts[i * 3 + 1], pts[i * 3 + 2]);
    const arr = grid.get(k);
    if (arr) arr.push(i);
    else grid.set(k, [i]);
  }
  const links: number[] = [];
  const linkColors: number[] = [];
  const maxLinks = ctx.light ? 900 : 2600;
  const a = new Vector3();
  const b = new Vector3();
  for (let i = 0; i < N && links.length / 6 < maxLinks; i++) {
    a.set(pts[i * 3], pts[i * 3 + 1], pts[i * 3 + 2]);
    const cx = Math.floor(a.x / cell);
    const cy = Math.floor(a.y / cell);
    const cz = Math.floor(a.z / cell);
    let made = 0;
    for (let dx = -1; dx <= 1 && made < 2; dx++)
      for (let dy = -1; dy <= 1 && made < 2; dy++)
        for (let dz = -1; dz <= 1 && made < 2; dz++) {
          const arr = grid.get(`${cx + dx},${cy + dy},${cz + dz}`);
          if (!arr) continue;
          for (const j of arr) {
            if (j <= i) continue;
            b.set(pts[j * 3], pts[j * 3 + 1], pts[j * 3 + 2]);
            if (a.distanceTo(b) < 1.25) {
              links.push(a.x, a.y, a.z, b.x, b.y, b.z);
              const gi = group[i] >= 0 ? group[i] : group[j];
              linkColors.push(gi, gi, gi, gi, gi, gi);
              made++;
              if (made >= 2) break;
            }
          }
        }
  }
  const linkGeo = geo(links);
  const linkColorArr = new Float32Array(links.length);
  const linkGroup = linkColors;
  const linkMat = makeMat("line", "deep", ctx.theme, { near: 1, far: 20, opacity: 0.45, useVertex: true });
  linkGeo.setAttribute("aColor", new BufferAttribute(linkColorArr, 3));
  const lines = new LineSegments(linkGeo, linkMat);
  mats.push(linkMat);

  const CN = ctx.light ? 60 : 140;
  const cp: number[] = [];
  const cs: number[] = [];
  for (let i = 0; i < CN; i++) {
    const v = new Vector3(rnd() - 0.5, rnd() - 0.5, rnd() - 0.5).normalize().multiplyScalar(Math.pow(rnd(), 0.6) * 1.1);
    cp.push(v.x, v.y, v.z);
    cs.push(0.9 + rnd() * 1.4);
  }
  const coreGeo = new BufferGeometry();
  coreGeo.setAttribute("position", new Float32BufferAttribute(cp, 3));
  coreGeo.setAttribute("aColor", new Float32BufferAttribute(new Array(CN * 3).fill(1), 3));
  coreGeo.setAttribute("aShade", new Float32BufferAttribute(new Array(CN).fill(1), 1));
  coreGeo.setAttribute("aSize", new Float32BufferAttribute(cs, 1));
  const coreMat = makeMat("point", "bright", ctx.theme, { near: 0.5, far: 30, opacity: 1, size: 2.6, dpr: ctx.dpr });
  const core = new Points(coreGeo, coreMat);
  mats.push(coreMat);

  const beamGeo = geo([0, 0, 0, 0, 0, 0]);
  const beamMat = makeMat("line", "bright", ctx.theme, { near: 0.5, far: 30, opacity: 0 });
  const beam = new Line(beamGeo, beamMat);
  const packetGeo = new BufferGeometry();
  packetGeo.setAttribute("position", new Float32BufferAttribute([0, 0, 0], 3));
  packetGeo.setAttribute("aColor", new Float32BufferAttribute([1, 1, 1], 3));
  packetGeo.setAttribute("aShade", new Float32BufferAttribute([1], 1));
  packetGeo.setAttribute("aSize", new Float32BufferAttribute([3], 1));
  const packetMat = makeMat("point", "bright", ctx.theme, { near: 0.5, far: 30, opacity: 0, size: 3, dpr: ctx.dpr });
  const packet = new Points(packetGeo, packetMat);
  mats.push(beamMat, packetMat);

  const glowMat = new ShaderMaterial({
    vertexShader: "varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
    fragmentShader:
      "precision mediump float; uniform vec3 uColor; uniform float uOpacity; varying vec2 vUv; void main(){ float d = length(vUv - 0.5) * 2.0; float al = pow(max(0.0, 1.0 - d), 2.6) * uOpacity; gl_FragColor = vec4(uColor * al, al); }",
    uniforms: { uColor: { value: new Color(ctx.theme === "light" ? PALETTE.light.line : PALETTE.dark.line) }, uOpacity: { value: 0 } },
    transparent: true,
    depthWrite: false,
    depthTest: false,
  });
  applyBlend(glowMat, ctx.theme);
  const glow = new Mesh(new PlaneGeometry(9, 9), glowMat);
  glow.position.z = -1.5;
  const brain = new Group();
  brain.add(glow, points, lines, core, beam, packet);
  scene.add(brain);

  let lastFocus = -2;
  const rot = { x: 0, y: 0 };

  const recolor = (focus: number, pulse: number) => {
    for (let i = 0; i < N; i++) {
      const on = group[i] === focus ? pulse : 0;
      const s = base[i * 3];
      colorArr[i * 3] = (cLine.r + (cBright.r * 1.4 - cLine.r) * on) * s;
      colorArr[i * 3 + 1] = (cLine.g + (cBright.g * 1.4 - cLine.g) * on) * s;
      colorArr[i * 3 + 2] = (cLine.b + (cBright.b * 1.4 - cLine.b) * on) * s;
    }
    g.attributes.aColor.needsUpdate = true;
    const cDeep = new Color(pal.deep);
    for (let i = 0; i < linkGroup.length; i++) {
      const on = linkGroup[i] === focus ? pulse : 0;
      linkColorArr[i * 3] = cDeep.r + (cBright.r - cDeep.r) * on;
      linkColorArr[i * 3 + 1] = cDeep.g + (cBright.g - cDeep.g) * on;
      linkColorArr[i * 3 + 2] = cDeep.b + (cBright.b - cDeep.b) * on;
    }
    linkGeo.attributes.aColor.needsUpdate = true;
  };
  recolor(-1, 0);

  return {
    kind: "brain",
    scene,
    camera,
    mats,
    update(p, t, alpha) {
      const sp = ctx.smoothPointer;
      const enter = smooth(0, ENTER_END, p);
      const inside = smooth(ENTER_END, REPORT_IN, p);
      const report = smooth(REPORT_IN, REPORT_PEAK, p);
      const z = 15 - 8.5 * enter - 4.5 * inside + 3 * report;
      camera.position.set(sp.x * 0.6, sp.y * 0.4, z);
      camera.lookAt(0, 0, 0);
      rot.y += (sp.x * 0.35 - rot.y) * 0.05;
      rot.x += (-sp.y * 0.25 - rot.x) * 0.05;
      brain.rotation.set(rot.x, rot.y + t * 0.04 + p * 0.5, 0);
      const fade = 1 - 0.7 * report;
      ptMat.uniforms.uOpacity.value = 0.9 * alpha * fade;
      linkMat.uniforms.uOpacity.value = 0.45 * alpha * fade * dim(ctx);
      const pulse = 0.5 + 0.5 * Math.sin(t * 3.2);
      glow.quaternion.copy(camera.quaternion);
      glowMat.uniforms.uOpacity.value = (ctx.theme === "light" ? 0.16 : 0.32) * (0.6 + 0.4 * inside) * (1 + 0.15 * pulse) * alpha * fade;
      coreMat.uniforms.uSize.value = 2.2 + 0.9 * pulse + 1.2 * report;
      coreMat.uniforms.uOpacity.value = alpha;
      const focus = focusAt(p);
      const strength = 0.7 + 0.3 * pulse;
      if (focus !== lastFocus || focus >= 0) recolor(focus, focus >= 0 ? strength : 0);
      lastFocus = focus;
      if (focus >= 0) {
        const an = anchors[focus];
        const pos = beamGeo.attributes.position as BufferAttribute;
        pos.setXYZ(1, an.x, an.y, an.z);
        pos.needsUpdate = true;
        beamMat.uniforms.uOpacity.value = 0.9 * alpha;
        const k = (t * 1.3) % 1;
        (packetGeo.attributes.position as BufferAttribute).setXYZ(0, an.x * k, an.y * k, an.z * k);
        packetGeo.attributes.position.needsUpdate = true;
        packetMat.uniforms.uOpacity.value = alpha;
      } else {
        beamMat.uniforms.uOpacity.value = 0;
        packetMat.uniforms.uOpacity.value = 0;
      }
    },
    dispose() {
      g.dispose();
      linkGeo.dispose();
      coreGeo.dispose();
      beamGeo.dispose();
      packetGeo.dispose();
      glow.geometry.dispose();
      glowMat.dispose();
      mats.forEach((m) => m.dispose());
    },
  };
}

function makeField(ctx: Ctx): Unit {
  const scene = new Scene();
  const camera = new PerspectiveCamera(60, 1, 0.1, 100);
  const mats: Mat[] = [];
  const S = 60;
  const step = 1.5;
  const pos: number[] = [];
  for (let x = -S / 2; x <= S / 2; x += step) pos.push(x, 0, 0, x, 0, -S);
  for (let z = 0; z >= -S; z -= step) pos.push(-S / 2, 0, z, S / 2, 0, z);
  const gridMat = makeMat("line", "deep", ctx.theme, { near: 3, far: 40, opacity: 0.5 });
  const grid = new LineSegments(geo(pos), gridMat);
  grid.position.y = -3.4;
  mats.push(gridMat);
  const PN = ctx.light ? 120 : 320;
  const pp: number[] = [];
  const ps: number[] = [];
  const rnd = mulberry(3);
  for (let i = 0; i < PN; i++) {
    pp.push((rnd() - 0.5) * 30, (rnd() - 0.5) * 10, -rnd() * 40);
    ps.push(0.5 + rnd());
  }
  const pg = new BufferGeometry();
  pg.setAttribute("position", new Float32BufferAttribute(pp, 3));
  pg.setAttribute("aColor", new Float32BufferAttribute(new Array(PN * 3).fill(1), 3));
  pg.setAttribute("aShade", new Float32BufferAttribute(new Array(PN).fill(1), 1));
  pg.setAttribute("aSize", new Float32BufferAttribute(ps, 1));
  const ptMat = makeMat("point", "line", ctx.theme, { near: 2, far: 40, opacity: 0.7, size: 1.6, dpr: ctx.dpr });
  const pts = new Points(pg, ptMat);
  mats.push(ptMat);
  scene.add(grid, pts);
  const cam = { x: 0, y: 0 };
  return {
    kind: "field",
    scene,
    camera,
    mats,
    update(p, t, alpha) {
      const sp = ctx.smoothPointer;
      grid.position.z = (p * 14 + t * 0.6) % step;
      pts.position.z = (p * 10 + t * 0.3) % 4;
      pts.position.y = Math.sin(t * 0.4) * 0.3;
      cam.x += (sp.x * 1.2 - cam.x) * 0.06;
      cam.y += (sp.y * 0.6 - cam.y) * 0.06;
      camera.position.set(cam.x, 0.4 + cam.y, 2);
      camera.rotation.set(-0.06 + sp.y * 0.05, -sp.x * 0.1, 0);
      gridMat.uniforms.uOpacity.value = 0.5 * alpha * dim(ctx);
      ptMat.uniforms.uOpacity.value = 0.7 * alpha;
    },
    dispose() {
      grid.geometry.dispose();
      pg.dispose();
      mats.forEach((m) => m.dispose());
    },
  };
}

const PARTICLE_VERT = `
attribute vec3 aSphere; attribute vec2 aText; attribute float aDelay; attribute float aSize;
uniform float uProgress; uniform float uTime; uniform float uSphereScale; uniform float uTextScale; uniform float uDpr; uniform float uSize;
uniform vec2 uPointer;
varying float vDepth; varying float vMix;
void main() {
  float t = smoothstep(aDelay * 0.35, aDelay * 0.35 + 0.65, uProgress);
  vMix = t;
  float ang = uTime * 0.22 + uPointer.x * 0.7;
  float c = cos(ang); float s = sin(ang);
  vec3 sp = aSphere * uSphereScale;
  sp = vec3(c * sp.x + s * sp.z, sp.y, -s * sp.x + c * sp.z);
  float tilt = -uPointer.y * 0.35; float ct = cos(tilt); float st = sin(tilt);
  sp = vec3(sp.x, ct * sp.y - st * sp.z, st * sp.y + ct * sp.z);
  vec3 tx = vec3(aText * uTextScale, 0.0);
  vec3 dir = normalize(sp + vec3(0.0, 0.0, 0.001));
  vec3 mid = mix(sp, tx, 0.5) + dir * 1.6 * (1.0 - abs(2.0 * t - 1.0));
  vec3 pos = mix(mix(sp, mid, t), mix(mid, tx, t), t);
  pos.y += sin(uTime * 1.3 + aDelay * 6.283) * 0.03 * (1.0 - t) + sin(uTime * 0.8 + aDelay * 6.283) * 0.012 * t;
  vec4 mv = modelViewMatrix * vec4(pos, 1.0);
  vDepth = clamp((pos.z + 3.5) / 7.0, 0.0, 1.0);
  gl_PointSize = uSize * aSize * uDpr * (1.0 + 0.35 * t) * min(3.0, 12.0 / max(1.0, -mv.z));
  gl_Position = projectionMatrix * mv;
}`;
const PARTICLE_FRAG = `
precision mediump float;
uniform vec3 uColor; uniform vec3 uDeep; uniform vec3 uBright; uniform float uOpacity; uniform float uSoft;
varying float vDepth; varying float vMix;
void main() {
  vec2 c = gl_PointCoord - 0.5;
  float d = length(c);
  float a = smoothstep(0.5, uSoft, d);
  vec3 col = mix(uDeep, uColor, vDepth);
  col = mix(col, uBright, vMix * 0.45);
  float al = a * uOpacity * (0.55 + 0.45 * vDepth);
  gl_FragColor = vec4(col * al, al);
}`;

function sampleText(lines: string[], font: string, rtl: boolean): Float32Array {
  const W = 1024;
  const H = 480;
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const g = canvas.getContext("2d", { willReadFrequently: true });
  if (!g) return new Float32Array(0);
  g.fillStyle = "#fff";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.direction = rtl ? "rtl" : "ltr";
  const fit = (text: string, weight: number, size: number, maxW: number) => {
    g.font = `${weight} ${size}px ${font}`;
    const w = g.measureText(text).width;
    if (w > maxW) {
      size = Math.floor((size * maxW) / w);
      g.font = `${weight} ${size}px ${font}`;
    }
    return size;
  };
  const s1 = fit(lines[0], 800, 190, W * 0.92);
  g.fillText(lines[0], W / 2, H * 0.34);
  if (lines[1]) {
    fit(lines[1], 500, Math.round(s1 * 0.36), W * 0.86);
    g.fillText(lines[1], W / 2, H * 0.34 + s1 * 0.62);
  }
  const data = g.getImageData(0, 0, W, H).data;
  const out: number[] = [];
  const step = 3;
  for (let y = 0; y < H; y += step) {
    for (let x = 0; x < W; x += step) {
      if (data[(y * W + x) * 4 + 3] > 120) out.push(((x - W / 2) / W) * 14, (-(y - H / 2) / W) * 14);
    }
  }
  return new Float32Array(out);
}

function makeParticles(ctx: Ctx, el: HTMLElement): Unit {
  const scene = new Scene();
  const camera = new PerspectiveCamera(50, 1, 0.1, 60);
  camera.position.set(0, 0, 10);
  const N = ctx.light ? 2600 : 6000;
  const rnd = mulberry(21);
  const sphere = new Float32Array(N * 3);
  const text = new Float32Array(N * 2);
  const delay = new Float32Array(N);
  const size = new Float32Array(N);
  const R = 3.3;
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < N; i++) {
    const y = 1 - (2 * (i + 0.5)) / N;
    const r = Math.sqrt(1 - y * y);
    const phi = i * golden;
    const shell = R * (0.86 + 0.14 * rnd());
    sphere[i * 3] = Math.cos(phi) * r * shell;
    sphere[i * 3 + 1] = y * shell;
    sphere[i * 3 + 2] = Math.sin(phi) * r * shell;
    text[i * 2] = sphere[i * 3];
    text[i * 2 + 1] = sphere[i * 3 + 1];
    delay[i] = rnd();
    size[i] = 0.7 + rnd() * 0.9;
  }
  const g = new BufferGeometry();
  g.setAttribute("position", new BufferAttribute(sphere, 3));
  g.setAttribute("aSphere", new BufferAttribute(sphere, 3));
  const textAttr = new BufferAttribute(text, 2);
  g.setAttribute("aText", textAttr);
  g.setAttribute("aDelay", new BufferAttribute(delay, 1));
  g.setAttribute("aSize", new BufferAttribute(size, 1));
  const pal = PALETTE[ctx.theme];
  const make = (sizeMul: number, opacity: number, soft: number) => {
    const m = new ShaderMaterial({
      vertexShader: PARTICLE_VERT,
      fragmentShader: PARTICLE_FRAG,
      uniforms: {
        uColor: { value: new Color(pal.line) },
        uDeep: { value: new Color(pal.deep) },
        uBright: { value: new Color(pal.bright) },
        uOpacity: { value: opacity },
        uSoft: { value: soft },
        uProgress: { value: 0 },
        uTime: { value: 0 },
        uSphereScale: { value: 1 },
        uTextScale: { value: 1 },
        uDpr: { value: ctx.dpr },
        uSize: { value: sizeMul },
        uPointer: { value: [0, 0] },
      },
      transparent: true,
      depthWrite: false,
      depthTest: false,
    }) as Mat;
    applyBlend(m, ctx.theme);
    m.userData.role = "line";
    return m;
  };
  const core = make(2.1, 0.95, 0.12);
  const glow = make(6.5, ctx.theme === "light" ? 0.05 : 0.09, 0.0);
  const mats: Mat[] = [core, glow];
  scene.add(new Points(g, glow), new Points(g, core));

  const lines = [el.dataset.line1 ?? "", el.dataset.line2 ?? ""].filter(Boolean);
  const title = el.querySelector<HTMLElement>("[data-particles-title]");
  const font = title ? getComputedStyle(title).fontFamily : "sans-serif";
  let disposed = false;
  const fill = () => {
    if (disposed) return;
    const pts = sampleText(lines, font, ctx.rtl);
    const count = pts.length / 2;
    if (!count) return;
    const order = new Uint32Array(count);
    for (let i = 0; i < count; i++) order[i] = i;
    for (let i = count - 1; i > 0; i--) {
      const j = Math.floor(rnd() * (i + 1));
      const tmp = order[i];
      order[i] = order[j];
      order[j] = tmp;
    }
    for (let i = 0; i < N; i++) {
      const k = order[i % count];
      const jit = i >= count ? 0.035 : 0.012;
      text[i * 2] = pts[k * 2] + (rnd() - 0.5) * jit;
      text[i * 2 + 1] = pts[k * 2 + 1] + (rnd() - 0.5) * jit;
    }
    textAttr.needsUpdate = true;
    el.dataset.particlesText = String(count);
  };
  (document.fonts?.ready ?? Promise.resolve()).then(fill).catch(fill);

  const cam = { x: 0, y: 0 };
  return {
    kind: "particles",
    scene,
    camera,
    mats,
    update(p, t, alpha) {
      const sp = ctx.smoothPointer;
      const visibleH = 2 * Math.tan((camera.fov * Math.PI) / 360) * 10;
      const visibleW = visibleH * camera.aspect;
      const textScale = Math.min(1, (visibleW * 0.92) / 14);
      const sphereScale = Math.min(1, (Math.min(visibleW, visibleH) * 0.8) / (2 * R));
      const progress = smooth(0.04, 0.86, p);
      cam.x += (sp.x * 0.5 - cam.x) * 0.06;
      cam.y += (sp.y * 0.3 - cam.y) * 0.06;
      camera.position.set(cam.x, cam.y, 10);
      camera.lookAt(0, 0, 0);
      for (const m of mats) {
        m.uniforms.uProgress.value = progress;
        m.uniforms.uTime.value = t;
        m.uniforms.uTextScale.value = textScale;
        m.uniforms.uSphereScale.value = sphereScale;
        (m.uniforms.uPointer.value as number[])[0] = sp.x;
        (m.uniforms.uPointer.value as number[])[1] = sp.y;
      }
      core.uniforms.uOpacity.value = 0.95 * alpha;
      glow.uniforms.uOpacity.value = (ctx.theme === "light" ? 0.05 : 0.09) * alpha;
    },
    dispose() {
      disposed = true;
      g.dispose();
      mats.forEach((m) => m.dispose());
    },
  };
}

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

export interface SceneSection {
  el: HTMLElement;
  kind: SceneKind;
  pinned: boolean;
}

export interface Engine {
  setSections(list: SceneSection[]): void;
  setTheme(theme: Theme): void;
  dispose(): void;
}

export function createEngine(canvas: HTMLCanvasElement, opts: { theme: Theme; light: boolean; rtl: boolean }): Engine | null {
  ColorManagement.enabled = false;
  let renderer: WebGLRenderer;
  try {
    renderer = new WebGLRenderer({ canvas, alpha: true, antialias: false, powerPreference: "low-power", preserveDrawingBuffer: true });
  } catch {
    return null;
  }
  const dpr = Math.min(window.devicePixelRatio || 1, opts.light ? 1 : 1.5);
  renderer.setPixelRatio(dpr);
  renderer.setClearColor(0x000000, 0);
  renderer.autoClear = false;

  const ctx: Ctx = { theme: opts.theme, light: opts.light, rtl: opts.rtl, dpr, pointer: sceneBus.pointer, smoothPointer: { x: 0, y: 0 } };
  const units = new Map<SceneKind, Unit>();
  const unitFor = (kind: SceneKind, el: HTMLElement): Unit => {
    let u = units.get(kind);
    if (!u) {
      u = kind === "brain" ? makeBrain(ctx) : kind === "field" ? makeField(ctx) : kind === "particles" ? makeParticles(ctx, el) : makeTunnel(ctx, kind === "warp");
      units.set(kind, u);
    }
    return u;
  };

  let sections: Live[] = [];
  type Live = SceneSection & { trigger?: ScrollTrigger };
  let current: { section: Live; unit: Unit } | null = null;
  let previous: { section: Live; unit: Unit; fade: number } | null = null;
  let raf = 0;
  let running = false;
  let width = 0;
  let height = 0;
  const clock = new Timer();

  const resize = () => {
    const w = window.innerWidth;
    const h = window.innerHeight;
    if (w === width && h === height) return;
    width = w;
    height = h;
    renderer.setSize(w, h, false);
    units.forEach((u) => {
      u.camera.aspect = w / h;
      u.camera.updateProjectionMatrix();
    });
  };

  const cover = (el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    const top = Math.max(0, r.top);
    const bottom = Math.min(height, r.bottom);
    return Math.max(0, bottom - top) / height;
  };

  const frame = () => {
    raf = 0;
    if (!running) return;
    resize();
    clock.update();
    const t = clock.getElapsed();
    ctx.smoothPointer.x += (ctx.pointer.x - ctx.smoothPointer.x) * 0.1;
    ctx.smoothPointer.y += (ctx.pointer.y - ctx.smoothPointer.y) * 0.1;

    let best: Live | null = null;
    let bestCover = 0;
    for (const s of sections) {
      const c = cover(s.el);
      if (c > bestCover) {
        bestCover = c;
        best = s;
      }
    }
    renderer.clear();
    if (!best || bestCover < 0.02) {
      if (current) {
        previous = { ...current, fade: 1 };
        current = null;
      }
    } else if (!current || current.section.el !== best.el) {
      if (current) previous = { ...current, fade: 1 };
      const unit = unitFor(best.kind, best.el);
      unit.camera.aspect = width / height;
      unit.camera.updateProjectionMatrix();
      current = { section: best, unit };
    }
    const dt = Math.min(0.05, clock.getDelta() || 0.016);
    if (previous) {
      previous.fade -= dt * 2.2;
      if (previous.fade <= 0 || (current && previous.unit === current.unit)) previous = null;
      else {
        const p = previous.section.pinned ? sceneBus.get(previous.section.el) : (previous.section.trigger?.progress ?? 0);
        previous.unit.update(p, t, previous.fade);
        renderer.render(previous.unit.scene, previous.unit.camera);
      }
    }
    if (current) {
      const p = current.section.pinned ? sceneBus.get(current.section.el) : (current.section.trigger?.progress ?? 0);
      const alpha = previous ? 1 - previous.fade * 0.5 : 1;
      current.unit.update(p, t, alpha);
      renderer.render(current.unit.scene, current.unit.camera);
    }
    if (current || previous) raf = requestAnimationFrame(frame);
    else running = false;
  };

  const wake = () => {
    if (document.hidden || raf) return;
    running = true;
    raf = requestAnimationFrame(frame);
  };
  const sleep = () => {
    running = false;
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
  };
  const onVisibility = () => (document.hidden ? sleep() : wake());
  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("scroll", wake, { passive: true });
  window.addEventListener("pointermove", wake, { passive: true });
  window.addEventListener("resize", wake);

  return {
    setSections(list) {
      sections.forEach((s) => s.trigger?.kill());
      sections = list.map((s) => ({
        ...s,
        trigger: s.pinned ? undefined : ScrollTrigger.create({ trigger: s.el, start: "top bottom", end: "bottom top" }),
      }));
      current = null;
      previous = null;
      wake();
    },
    setTheme(theme) {
      ctx.theme = theme;
      const pal = PALETTE[theme];
      units.forEach((u) => {
        u.mats.forEach((m) => {
          (m.uniforms.uColor.value as Color).set(pal[m.userData.role]);
          if (m.uniforms.uDeep) (m.uniforms.uDeep.value as Color).set(pal.deep);
          if (m.uniforms.uBright) (m.uniforms.uBright.value as Color).set(pal.bright);
          applyBlend(m, theme);
        });
        if (u.kind === "brain") {
          u.dispose();
          units.delete("brain");
          if (current?.unit === u) current = null;
        }
        u.scene.traverse((o) => {
          const mesh = o as Mesh;
          const mat = mesh.material as ShaderMaterial | undefined;
          if (mat && mat.uniforms?.uColor && !u.mats.includes(mat as Mat)) {
            (mat.uniforms.uColor.value as Color).set(theme === "light" ? pal.line : pal.bright);
            applyBlend(mat, theme);
          }
        });
      });
      wake();
    },
    dispose() {
      sleep();
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("scroll", wake);
      window.removeEventListener("pointermove", wake);
      window.removeEventListener("resize", wake);
      sections.forEach((s) => s.trigger?.kill());
      units.forEach((u) => u.dispose());
      units.clear();
      renderer.dispose();
    },
  };
}
