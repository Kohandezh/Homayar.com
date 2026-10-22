import { Object3D, AddEquation, BufferAttribute, BufferGeometry, CatmullRomCurve3, Color, ColorManagement, CustomBlending, Float32BufferAttribute, Group, Line, LineSegments, Material, Mesh, OneFactor, OneMinusSrcAlphaFactor, PerspectiveCamera, PlaneGeometry, Points, Scene, ShaderMaterial, Timer, Vector3, WebGLRenderer } from "three";
import { ScrollTrigger } from "@/lib/gsap";
import { sceneBus, type SceneKind } from "./bus";
import { BRAIN_PASS, ENTER_END, EXHIBIT_ARRIVE, EXHIBIT_FACE, EXHIBIT_LEAVE, focusAt, REPORT_IN } from "./timing";

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

// The distance fade runs per pixel. Long lines have only two points, so a per-point fade would not reach the middle.
const LINE_VERT = `
attribute vec3 aColor; attribute float aShade;
varying float vDist; varying float vShade; varying vec3 vColor;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vDist = -mv.z;
  vShade = aShade;
  vColor = aColor;
  gl_Position = projectionMatrix * mv;
}`;
const LINE_FRAG = `
precision mediump float;
uniform vec3 uColor; uniform float uOpacity; uniform float uUseVertex; uniform float uNear; uniform float uFar;
varying float vDist; varying float vShade; varying vec3 vColor;
void main() {
  vec3 c = mix(uColor, vColor, uUseVertex);
  float a = uOpacity * (1.0 - smoothstep(uNear, uFar, vDist)) * vShade;
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

interface Pose {
  // Camera height above the floor.
  alt: number;
  // Nose up (+) or down (-), in radians.
  pitch: number;
  // Wing tilt, in radians.
  roll: number;
  // How much of the runway shows (0 to 1).
  runway: number;
  // Turn left (+) or right (-), in radians.
  yaw?: number;
  // Where the camera is over the floor (x sideways, z forward is negative). Without it, the floor moves with the page scroll.
  ground?: { x: number; z: number };
  // Extra distance the floor stays visible, for high views that look down.
  view?: number;
  // Use the pose as it is. The scene already smooths it, and its own objects must match the camera exactly.
  exact?: boolean;
  // How strong the speed lines are (0 to 1). They only make sense when the camera moves forward.
  wind?: number;
  // How much the mouse moves the camera (0 to 1). 0 keeps the view still, so HTML content can line up with 3D objects.
  sway?: number;
}

interface Unit {
  kind: SceneKind;
  scene: Scene;
  camera: PerspectiveCamera;
  mats: Mat[];
  // Floor scenes return where the shared camera should be. The engine glides the camera there.
  pose?(p: number): Pose;
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
  // One camera for every floor scene, so the floor and the objects on it always line up.
  cam: PerspectiveCamera;
  // How far the floor has moved. It follows the page scroll.
  travel: number;
  // Current camera height above the floor and nose angle, without pointer sway.
  alt: number;
  pitch: number;
  // How far the landing part of the page is scrolled, 0 to 1. It starts at the first "field" section and ends at the page bottom.
  descent: number;
  // How far the whole page is scrolled, 0 to 1.
  page: number;
}

const TUNNEL_DIST = 90;
const FLOOR_H = 4.5;
const FLOOR_L = 100;
// Size of one grid square.
const CELL = 1;
// A brighter line every this many units. It must be a whole number of cells.
const FLOOR_PERIOD = 8;
// The floor moves this many units per viewport height of scroll.
const FLOOR_SPEED = TUNNEL_DIST / 2.6;
// Scenes that share one floor grid and one camera, so the floor never pops in or jumps between them.
const FLOOR_KINDS: SceneKind[] = ["particles", "tunnel", "warp", "brain", "field", "ride", "map", "vault", "gallery", "orbit", "lift", "beacon", "dome", "stacks"];
// The brain starts to show when its section is this many screen heights below the top.
const BRAIN_APPROACH = 2.2;
const CRUISE: Pose = { alt: FLOOR_H - 0.6, pitch: -0.06, roll: 0, runway: 0 };
// Camera height when the wheels are on the runway.
const GROUND = 0.8;
// Camera height after the hero take-off. The sphere floats at this height.
const SKY = 6;
// The flight section climbs to this height, then the next section levels off at HIGH.
const CLIMB = 10;
const HIGH = 14;

// Smooth value along keyframes [progress, value], sorted by progress.
function track(p: number, keys: Array<[number, number]>) {
  if (p <= keys[0][0]) return keys[0][1];
  for (let i = 1; i < keys.length; i++) {
    const [p1, v1] = keys[i];
    const [p0, v0] = keys[i - 1];
    if (p <= p1) return v0 + (v1 - v0) * smooth(p0, p1, p);
  }
  return keys[keys.length - 1][1];
}

function floorCamera() {
  const cam = new PerspectiveCamera(64, 1, 0.1, 160);
  // Turn first, then tilt the nose, then bank. This keeps a turning camera level.
  cam.rotation.order = "YXZ";
  return cam;
}

function wrap(v: number, period: number) {
  return ((v % period) + period) % period;
}

function buildFloor(ctx: Ctx) {
  // Wide enough that the lines reach the screen edges at the horizon, even on ultra-wide screens.
  const FW = 160;
  const pos: number[] = [];
  // The floor also reaches behind the camera, so a view that looks straight down never sees its edge.
  const BACK = 44;
  for (let x = -FW; x <= FW + 0.001; x += CELL) pos.push(x, -FLOOR_H, BACK, x, -FLOOR_H, -FLOOR_L);
  const longMat = makeMat("line", "deep", ctx.theme, { near: 6, far: 65, opacity: 0.6 });
  const long = new LineSegments(geo(pos), longMat);

  const rp: number[] = [];
  const rs: number[] = [];
  for (let z = BACK; z >= -FLOOR_L; z -= CELL) {
    const idx = Math.round((BACK - z) / CELL);
    const gate = idx % (FLOOR_PERIOD / CELL) === 0;
    rp.push(-FW, -FLOOR_H, z, FW, -FLOOR_H, z);
    const shade = gate ? 1 : 0.55;
    rs.push(shade, shade);
  }
  const ringMat = makeMat("line", "line", ctx.theme, { near: 4, far: 50, opacity: 0.85 });
  const rings = new LineSegments(geo(rp, undefined, rs), ringMat);
  const group = new Group();
  group.add(long, rings);
  return {
    group,
    mats: [longMat, ringMat],
    update(travel: number, alpha: number, boost = 0, x = 0, view = 0) {
      group.position.z = wrap(travel, FLOOR_PERIOD);
      group.position.x = -wrap(x, CELL);
      longMat.uniforms.uFar.value = 65 + view;
      ringMat.uniforms.uFar.value = 50 + view;
      // Seen from high above, the floor is thinner, so it reads as a map and not as a dense mesh.
      alpha *= 1 - 0.6 * smooth(10, 50, view);
      longMat.uniforms.uOpacity.value = 0.6 * alpha * dim(ctx);
      ringMat.uniforms.uOpacity.value = (0.75 + 0.25 * boost) * alpha * dim(ctx);
    },
  };
}

// Speed lines ("wind"): short lines all around the camera that rush past while the page scrolls.
// They sit around the camera, not on the ground, so they show at any height.
function buildStreaks(ctx: Ctx) {
  const SN = ctx.light ? 30 : 70;
  const DEPTH = 74;
  const rnd = mulberry(5);
  const base: number[] = [];
  for (let i = 0; i < SN; i++) {
    const a = rnd() * Math.PI * 2;
    const r = 2.4 + rnd() * 7;
    base.push(Math.cos(a) * r * 1.5, Math.sin(a) * r * 0.8, -rnd() * DEPTH);
  }
  const pos = new Float32Array(SN * 6);
  const g = new BufferGeometry();
  g.setAttribute("position", new BufferAttribute(pos, 3));
  g.setAttribute("aColor", new Float32BufferAttribute(new Array(SN * 6).fill(1), 3));
  g.setAttribute("aShade", new Float32BufferAttribute(new Array(SN * 2).fill(1), 1));
  const mat = makeMat("line", "bright", ctx.theme, { near: 2, far: DEPTH, opacity: 0 });
  const lines = new LineSegments(g, mat);
  lines.frustumCulled = false;
  return {
    group: lines,
    mats: [mat],
    update(travel: number, speed: number, alpha: number) {
      const camY = -FLOOR_H + ctx.alt;
      const len = 0.5 + speed * 7;
      for (let i = 0; i < SN; i++) {
        const z = wrap(base[i * 3 + 2] + travel * 1.6 + 70, DEPTH) - 70;
        const x = base[i * 3];
        const y = Math.max(-FLOOR_H + 0.1, camY + base[i * 3 + 1]);
        pos.set([x, y, z, x, y, z + len], i * 6);
      }
      g.attributes.position.needsUpdate = true;
      mat.uniforms.uOpacity.value = smooth(0.08, 0.7, speed) * 0.9 * alpha;
    },
  };
}

// The flight section: the camera stays in the air, nose up, and keeps climbing.
function makeTunnel(ctx: Ctx): Unit {
  const scene = new Scene();
  const camera = ctx.cam;
  const mats: Mat[] = [];

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
  scene.add(wavePts, waveLine);

  return {
    kind: "tunnel",
    scene,
    camera,
    mats,
    pose(p) {
      return {
        alt: track(p, [
          [0, SKY],
          [1, CLIMB],
        ]),
        pitch: track(p, [
          [0, CRUISE.pitch],
          [0.15, 0.1],
        ]),
        roll: 0,
        runway: 1,
      };
    },
    update(p, t, alpha) {
      const sp = ctx.smoothPointer;
      const env = 0.55 + 0.45 * Math.sin(t * 0.7);
      const px = sp.x * 6;
      // The wave rides with the camera height and nose angle, so it stays in the same screen spot while the camera climbs.
      const lift = ctx.cam.position.y + 0.6 + Math.tan(ctx.pitch - CRUISE.pitch) * 16;
      for (let i = 0; i < N; i++) {
        const x = -5.5 + (11 * i) / (N - 1);
        const boost = 1 + 2.2 * Math.exp(-((x - px) * (x - px)) / 2.2);
        const y = (Math.sin(x * 1.7 + t * 1.8) * 0.6 * env + Math.sin(x * 4.3 - t * 2.6) * 0.28 + Math.sin(x * 9.1 + t * 4.1) * 0.1) * boost;
        wavePos[i * 3] = x;
        wavePos[i * 3 + 1] = y - sp.y * 0.6 - (ctx.light ? 1.9 : 0) + lift;
        wavePos[i * 3 + 2] = -16;
      }
      waveGeo.attributes.position.needsUpdate = true;
      wavePtMat.uniforms.uOpacity.value = 0.95 * alpha;
      waveLnMat.uniforms.uOpacity.value = 0.8 * alpha;
    },
    dispose() {
      scene.traverse((o) => {
        const m = o as Mesh;
        m.geometry?.dispose();
      });
      mats.forEach((m) => m.dispose());
    },
  };
}

// Runway on the floor: edge lights, edge lines and a dashed center line. It moves with the floor.
function buildRunway(ctx: Ctx) {
  const y = -FLOOR_H + 0.02;
  const HALF = 2.4;
  const GAP = 2;

  const lp: number[] = [];
  for (let z = 4; z >= -FLOOR_L; z -= GAP) lp.push(-HALF, y, z, HALF, y, z);
  const lightGeo = new BufferGeometry();
  lightGeo.setAttribute("position", new Float32BufferAttribute(lp, 3));
  lightGeo.setAttribute("aColor", new Float32BufferAttribute(new Array(lp.length).fill(1), 3));
  lightGeo.setAttribute("aShade", new Float32BufferAttribute(new Array(lp.length / 3).fill(1), 1));
  lightGeo.setAttribute("aSize", new Float32BufferAttribute(new Array(lp.length / 3).fill(1), 1));
  const lightMat = makeMat("point", "bright", ctx.theme, { near: 1, far: 70, opacity: 0, size: 2.6, dpr: ctx.dpr });
  const lights = new Points(lightGeo, lightMat);

  const dp: number[] = [];
  for (let z = 4; z >= -FLOOR_L; z -= GAP) dp.push(0, y, z, 0, y, z - GAP / 2);
  for (const x of [-HALF, HALF]) dp.push(x, y, 4, x, y, -FLOOR_L);
  const lineMat = makeMat("line", "bright", ctx.theme, { near: 1, far: 60, opacity: 0 });
  const lines = new LineSegments(geo(dp), lineMat);

  const group = new Group();
  group.add(lines, lights);
  return {
    group,
    mats: [lightMat, lineMat],
    update(travel: number, show: number) {
      group.position.z = wrap(travel, GAP);
      lightMat.uniforms.uOpacity.value = 0.95 * show;
      lineMat.uniforms.uOpacity.value = 0.7 * show * dim(ctx);
    },
  };
}

// After take-off: the plane keeps climbing, then levels off high above the grid.
function makeClimb(ctx: Ctx): Unit {
  return {
    kind: "warp",
    scene: new Scene(),
    camera: ctx.cam,
    mats: [],
    pose(p) {
      return {
        alt: track(p, [
          [0, CLIMB],
          [0.5, HIGH],
        ]),
        pitch: track(p, [
          [0, 0.1],
          [0.45, -0.05],
        ]),
        roll: 0.03 * smooth(0.1, 0.3, p) * (1 - smooth(0.35, 0.5, p)),
        runway: 1 - smooth(0, 0.3, p),
      };
    },
    update() {},
    dispose() {},
  };
}

// How far away the brain is when the approach starts.
const BRAIN_FAR = 60;

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
    // The brain floats high in the sky. The camera cruises there, above the grid.
    pose: () => ({ alt: HIGH, pitch: -0.03, roll: 0, runway: 0 }),
    update(p, t, alpha) {
      const sp = ctx.smoothPointer;
      // Negative p is the approach before the section pins: -1 is far away, 0 is arrived.
      const far = Math.max(0, -p);
      alpha *= 1 - smooth(0.8, 1, far);
      ptMat.uniforms.uFar.value = 22 + BRAIN_FAR * far;
      linkMat.uniforms.uFar.value = 20 + BRAIN_FAR * far;
      coreMat.uniforms.uFar.value = 30 + BRAIN_FAR * far;
      const enter = smooth(0, ENTER_END, p);
      const inside = smooth(ENTER_END, REPORT_IN, p);
      // After the conversation the camera flies through the brain and leaves it behind.
      const pass = smooth(REPORT_IN, BRAIN_PASS, p);
      const z = 15 - 8.5 * enter - 4.5 * inside - 16 * pass + BRAIN_FAR * far;
      camera.position.set(sp.x * 0.6, sp.y * 0.4, z);
      // Look at the brain while in front of it, then keep looking forward once inside it.
      camera.lookAt(0, 0, z - Math.max(6, z));
      rot.y += (sp.x * 0.35 - rot.y) * 0.05;
      rot.x += (-sp.y * 0.25 - rot.x) * 0.05;
      brain.rotation.set(rot.x, rot.y + t * 0.04 + p * 0.5, 0);
      // The brain is fully gone halfway through the fly-through, before the report comes in.
      const fade = 1 - smooth(REPORT_IN, REPORT_IN + (BRAIN_PASS - REPORT_IN) * 0.5, p);
      ptMat.uniforms.uOpacity.value = 0.9 * alpha * fade;
      linkMat.uniforms.uOpacity.value = 0.45 * alpha * fade * dim(ctx);
      const pulse = 0.5 + 0.5 * Math.sin(t * 3.2);
      glow.quaternion.copy(camera.quaternion);
      glowMat.uniforms.uOpacity.value = (ctx.theme === "light" ? 0.16 : 0.32) * (0.6 + 0.4 * inside) * (1 + 0.15 * pulse) * alpha * fade;
      coreMat.uniforms.uSize.value = 2.2 + 0.9 * pulse;
      coreMat.uniforms.uOpacity.value = alpha * fade;
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

// The landing: after the brain, the camera slowly descends across the remaining sections,
// flares near the end of the page and touches down on the runway at the final call to action.
// A star sky that follows the camera height, for scenes that are not the home page.
function buildSky(ctx: Ctx, seed: number) {
  const N = ctx.light ? 700 : 1600;
  const rnd = mulberry(seed);
  const pos: number[] = [];
  const sizes: number[] = [];
  for (let i = 0; i < N; i++) {
    const az = rnd() * Math.PI * 2;
    const el = 0.04 + Math.pow(rnd(), 0.8) * 0.9;
    const dist = 40 + rnd() * 60;
    pos.push(Math.sin(az) * Math.cos(el) * dist, Math.sin(el) * dist, -Math.cos(az) * Math.cos(el) * dist);
    sizes.push(0.5 + rnd() * 1.2);
  }
  const g = new BufferGeometry();
  g.setAttribute("position", new Float32BufferAttribute(pos, 3));
  g.setAttribute("aColor", new Float32BufferAttribute(new Array(N * 3).fill(1), 3));
  g.setAttribute("aShade", new Float32BufferAttribute(new Array(N).fill(1), 1));
  g.setAttribute("aSize", new Float32BufferAttribute(sizes, 1));
  const mat = makeMat("point", "bright", ctx.theme, { near: 1, far: 420, opacity: 0, size: 2.6, dpr: ctx.dpr });
  const stars = new Points(g, mat);
  stars.frustumCulled = false;
  const group = new Group();
  group.add(stars);
  return {
    group,
    mats: [mat],
    update(t: number, alpha: number) {
      // The sky stays around the camera, so the stars never sink below the horizon.
      group.position.y = ctx.cam.position.y;
      mat.uniforms.uOpacity.value = (0.75 + 0.1 * Math.sin(t * 0.8)) * alpha;
    },
    dispose() {
      g.dispose();
      mat.dispose();
    },
  };
}

// Edges of a wire box, for small objects in the world.
function boxLines(w: number, h: number, d: number) {
  const x = w / 2;
  const z = d / 2;
  const c = [
    [-x, 0, -z], [x, 0, -z], [x, 0, z], [-x, 0, z],
    [-x, h, -z], [x, h, -z], [x, h, z], [-x, h, z],
  ];
  const e = [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]];
  const out: number[] = [];
  for (const [a, b] of e) out.push(...c[a], ...c[b]);
  return out;
}

// Page scroll (0 to 1) where each element sits in the middle of the screen.
function centers(els: HTMLElement[]) {
  const max = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
  return els.map((e) => {
    const r = e.getBoundingClientRect();
    return Math.min(1, Math.max(0, (r.top + window.scrollY + r.height / 2 - window.innerHeight / 2) / max));
  });
}

// how-it-works: the camera rides the ribbon like a track. Each of the seven steps is a gate on the track.
// The gate passes the camera when its text is in the middle of the screen. After the last gate the track climbs into the sky.
function makeRide(ctx: Ctx, el: HTMLElement): Unit {
  const scene = new Scene();
  const mats: Mat[] = [];
  const sky = buildSky(ctx, 41);
  mats.push(...sky.mats);
  const cards = () => Array.from(el.querySelectorAll<HTMLElement>("[data-snake-card]"));
  const n = Math.max(1, cards().length);
  const STEP = 34;
  const floorY = -FLOOR_H;
  // The track as a smooth curve: gentle hills and turns. Point k sits STEP units after point k-1.
  const ctrl: Vector3[] = [];
  for (let k = 0; k <= n + 2; k++) {
    const z = -k * STEP;
    const x = k === 0 ? 0 : Math.sin(k * 1.7) * 9;
    const hill = k === 0 ? 0.6 : 2.2 + 1.8 * Math.sin(k * 2.3);
    const climb = k > n ? (k - n) * 9 : 0;
    ctrl.push(new Vector3(x, floorY + hill + climb, z));
  }
  const curve = new CatmullRomCurve3(ctrl, false, "catmullrom", 0.5);
  // Gate k sits at curve point k+1. tAt maps a control point index to the curve parameter.
  const tAt = (k: number) => k / (ctrl.length - 1);

  const SAMPLES = 900;
  const rail: number[] = [];
  const ties: number[] = [];
  const center: number[] = [];
  const up = new Vector3(0, 1, 0);
  const side = new Vector3();
  let prev: Vector3 | null = null;
  for (let i = 0; i <= SAMPLES; i++) {
    const t = i / SAMPLES;
    const p = curve.getPointAt(t);
    const tan = curve.getTangentAt(t);
    side.crossVectors(tan, up).normalize().multiplyScalar(0.8);
    const l = p.clone().add(side);
    const r = p.clone().sub(side);
    if (prev) {
      const pt = curve.getPointAt((i - 1) / SAMPLES);
      const pTan = curve.getTangentAt((i - 1) / SAMPLES);
      const ps = new Vector3().crossVectors(pTan, up).normalize().multiplyScalar(0.8);
      rail.push(...pt.clone().add(ps).toArray(), ...l.toArray(), ...pt.clone().sub(ps).toArray(), ...r.toArray());
      center.push(...pt.toArray(), ...p.toArray());
    }
    if (i % 3 === 0) ties.push(...l.toArray(), ...r.toArray());
    prev = p;
  }
  const railMat = makeMat("line", "bright", ctx.theme, { near: 1, far: 70, opacity: 0.9 });
  const tieMat = makeMat("line", "line", ctx.theme, { near: 1, far: 50, opacity: 0.5 });
  const glowMat = makeMat("line", "bright", ctx.theme, { near: 1, far: 90, opacity: 0.5 });
  const rails = new LineSegments(geo(rail), railMat);
  const tieLines = new LineSegments(geo(ties), tieMat);
  const centerLine = new LineSegments(geo(center), glowMat);
  mats.push(railMat, tieMat, glowMat);

  // Gates: a frame around the track at each step. The one being passed glows.
  const gateMats: Mat[] = [];
  const track = new Group();
  track.add(rails, tieLines, centerLine);
  for (let k = 0; k < n; k++) {
    const t = tAt(k + 1);
    const p = curve.getPointAt(t);
    const tan = curve.getTangentAt(t);
    const m = makeMat("line", "bright", ctx.theme, { near: 1, far: 90, opacity: 0.4 });
    const W = 2.6;
    const H = 3.2;
    const frame = new LineSegments(geo([-W, 0, 0, -W, H, 0, -W, H, 0, W, H, 0, W, H, 0, W, 0, 0, -W, -0.4, 0, W, -0.4, 0]), m);
    frame.position.copy(p);
    frame.rotation.y = Math.atan2(-tan.x, -tan.z);
    track.add(frame);
    gateMats.push(m);
  }
  mats.push(...gateMats);
  scene.add(sky.group, track);
  [rails, tieLines, centerLine].forEach((o) => (o.frustumCulled = false));

  let s = -1;
  let stops: number[] = [];
  let measured = 0;
  const cam = new Vector3();
  const ahead = new Vector3();
  return {
    kind: "ride",
    scene,
    camera: ctx.cam,
    mats,
    pose() {
      // Measure the step positions once per second, because the page layout can change.
      if (performance.now() - measured > 1000) {
        stops = centers(cards());
        measured = performance.now();
      }
      // Map page scroll to the track: before the first gate, between gates, after the last one.
      const d = ctx.page;
      const keys: Array<[number, number]> = [[0, 0]];
      stops.forEach((f, k) => keys.push([f, tAt(k + 1)]));
      keys.push([1, 1]);
      let target = 0;
      for (let i = 1; i < keys.length; i++) {
        if (d <= keys[i][0] || i === keys.length - 1) {
          const [f0, t0] = keys[i - 1];
          const [f1, t1] = keys[i];
          target = t0 + (t1 - t0) * Math.min(1, Math.max(0, (d - f0) / Math.max(1e-4, f1 - f0)));
          break;
        }
      }
      // Stop just before the end of the track, so the camera always has track ahead of it.
      target = Math.min(0.985, target);
      s = s < 0 ? target : s + (target - s) * 0.1;
      curve.getPointAt(s, cam);
      curve.getPointAt(Math.min(1, s + 0.012), ahead);
      const dx = ahead.x - cam.x;
      const dz = ahead.z - cam.z;
      const dy = ahead.y - cam.y;
      const yaw = Math.atan2(-dx, -dz);
      // Bank into the turn, like a car on a curved track.
      const bend = curve.getTangentAt(Math.min(1, s + 0.03));
      const turn = Math.atan2(-bend.x, -bend.z) - yaw;
      return {
        alt: cam.y - floorY + 1.3,
        pitch: Math.atan2(dy, Math.hypot(dx, dz)) - 0.05,
        roll: Math.max(-0.25, Math.min(0.25, turn * 1.6)),
        yaw,
        runway: 0,
        ground: { x: cam.x, z: cam.z },
        exact: true,
      };
    },
    update(_p, t, alpha) {
      // The world moves so the camera sits at the origin, on top of the track.
      track.position.set(-cam.x, 0, -cam.z);
      sky.update(t, alpha);
      railMat.uniforms.uOpacity.value = 0.9 * alpha;
      tieMat.uniforms.uOpacity.value = 0.5 * alpha * dim(ctx);
      glowMat.uniforms.uOpacity.value = (0.45 + 0.15 * Math.sin(t * 3)) * alpha;
      gateMats.forEach((m, k) => {
        const near = 1 - Math.min(1, Math.abs(s - tAt(k + 1)) / (tAt(1) * 0.6));
        m.uniforms.uOpacity.value = (0.35 + 0.65 * near) * alpha;
      });
    },
    dispose() {
      scene.traverse((o) => (o as Mesh).geometry?.dispose());
      mats.forEach((m) => m.dispose());
    },
  };
}

// deployment: the floor is a map seen from high above. Three zones lie on it side by side.
// While the zones section is on screen, the camera comes down to the zone the Homayar block is in.
function makeMap(ctx: Ctx, el: HTMLElement): Unit {
  const scene = new Scene();
  const mats: Mat[] = [];
  const sky = buildSky(ctx, 53);
  mats.push(...sky.mats);
  const y = -FLOOR_H + 0.03;
  const SIZE = 14;
  const GAP = 24;
  // In Persian the zone columns run right to left, so the map does too.
  const xs = ctx.rtl ? [GAP, 0, -GAP] : [-GAP, 0, GAP];
  const world = new Group();
  const zoneMats: Mat[] = [];
  const half = SIZE / 2;
  const square = (x0: number, x1: number) => [x0, y, -half, x1, y, -half, x1, y, -half, x1, y, half, x1, y, half, x0, y, half, x0, y, half, x0, y, -half];
  xs.forEach((x, i) => {
    const m = makeMat("line", "bright", ctx.theme, { near: 1, far: 140, opacity: 0.4 });
    const parts: number[] = [];
    // Cloud: one square with a cloud of points over it.
    // Your server: one square with a fence around it, because no traffic leaves.
    // Hybrid: the square is split in two halves.
    if (i === 2) parts.push(...square(-half, -0.8), ...square(0.8, half));
    else parts.push(...square(-half, half));
    if (i === 1) {
      for (let k = -half; k <= half + 0.01; k += 1) {
        parts.push(k, y, -half, k, y + 1.6, -half, k, y, half, k, y + 1.6, half, -half, y, k, -half, y + 1.6, k, half, y, k, half, y + 1.6, k);
      }
      parts.push(...square(-half, half).map((v, j) => (j % 3 === 1 ? v + 1.6 : v)));
    }
    const lines = new LineSegments(geo(parts), m);
    lines.position.x = x;
    world.add(lines);
    zoneMats.push(m);
  });
  const rnd = mulberry(61);
  const cp: number[] = [];
  for (let k = 0; k < 220; k++) {
    const a = rnd() * Math.PI * 2;
    const r = Math.sqrt(rnd()) * 5;
    cp.push(xs[0] + Math.cos(a) * r * 1.2, y + 5 + (rnd() - 0.5) * 2.4 * (1 - r / 6), Math.sin(a) * r * 0.8);
  }
  const cg = new BufferGeometry();
  cg.setAttribute("position", new Float32BufferAttribute(cp, 3));
  cg.setAttribute("aColor", new Float32BufferAttribute(new Array(cp.length).fill(1), 3));
  cg.setAttribute("aShade", new Float32BufferAttribute(new Array(cp.length / 3).fill(1), 1));
  cg.setAttribute("aSize", new Float32BufferAttribute(new Array(cp.length / 3).fill(1.2), 1));
  const cloudMat = makeMat("point", "bright", ctx.theme, { near: 1, far: 140, opacity: 0.6, size: 2.4, dpr: ctx.dpr });
  const cloud = new Points(cg, cloudMat);
  world.add(cloud);
  // The Homayar block: a small glowing box that flies to the active zone.
  const blockMat = makeMat("line", "bright", ctx.theme, { near: 1, far: 140, opacity: 1 });
  const block = new LineSegments(geo(boxLines(2, 2, 2)), blockMat);
  world.add(block);
  mats.push(...zoneMats, cloudMat, blockMat);
  scene.add(sky.group, world);
  world.traverse((o) => (o.frustumCulled = false));

  const zones = el.querySelector<HTMLElement>("[data-zones]");
  const st = { x: 0, z: 10, alt: 60, pitch: -1.5, bx: 0 };
  let ready = false;
  return {
    kind: "map",
    scene,
    camera: ctx.cam,
    mats,
    pose() {
      const r = zones?.getBoundingClientRect();
      const vh = window.innerHeight;
      const active = Number(el.querySelector<HTMLElement>(".zone.is-active")?.dataset.zone ?? 0);
      let want = { x: 0, z: 10, alt: 60, pitch: -1.5 };
      if (r && r.top < vh * 0.5 && r.bottom > vh * 0.5) {
        // Zones on screen: come down in front of the active zone and look at it.
        want = { x: xs[active], z: 20, alt: 16, pitch: -0.62 };
      } else if (r && r.bottom <= vh * 0.5) {
        // After the zones: climb back up and keep moving forward over the map.
        want = { x: 0, z: 34 - (ctx.page - 0.5) * 40, alt: 34, pitch: -0.9 };
      }
      const k = ready ? 0.05 : 1;
      ready = true;
      st.x += (want.x - st.x) * k;
      st.z += (want.z - st.z) * k;
      st.alt += (want.alt - st.alt) * k;
      st.pitch += (want.pitch - st.pitch) * k;
      st.bx += (xs[active] - st.bx) * 0.08;
      return { alt: st.alt, pitch: st.pitch, roll: 0, yaw: 0, runway: 0, ground: { x: st.x, z: st.z }, view: st.alt * 1.6, exact: true };
    },
    update(_p, t, alpha) {
      world.position.set(-st.x, 0, -st.z);
      sky.update(t, alpha);
      const active = Number(el.querySelector<HTMLElement>(".zone.is-active")?.dataset.zone ?? 0);
      zoneMats.forEach((m, i) => (m.uniforms.uOpacity.value = (i === active ? 1 : 0.35) * alpha));
      cloudMat.uniforms.uOpacity.value = (active === 0 ? 0.8 : 0.35) * alpha;
      block.position.set(st.bx, y + 3 + 0.4 * Math.sin(t * 1.6), 0);
      block.rotation.y = t * 0.5;
      blockMat.uniforms.uOpacity.value = alpha;
    },
    dispose() {
      scene.traverse((o) => (o as Mesh).geometry?.dispose());
      mats.forEach((m) => m.dispose());
    },
  };
}

// security: the camera tilts to look straight down and drops through the floor into a shaft.
// Each security layer is a grid plate with a hole in the middle. The camera passes one plate per layer card.
// At the bottom the audio file sits locked in a cage, and the camera circles it for the rest of the page.
function makeVault(ctx: Ctx, el: HTMLElement): Unit {
  const scene = new Scene();
  const mats: Mat[] = [];
  const sky = buildSky(ctx, 71);
  mats.push(...sky.mats);
  const section = el.querySelector<HTMLElement>("[data-layers]");
  const n = Math.max(1, el.querySelectorAll("[data-layer]").length);
  const STEP = 9;
  const top = -FLOOR_H;
  const plateY = (k: number) => top - (k + 1) * STEP;
  const coreY = plateY(n) - 4;
  const R = 15;
  const HOLE = 3;
  const world = new Group();

  // Shaft: lines down the four corners and the middle of each wall, from the floor to the core.
  const shaft: number[] = [];
  for (const [x, z] of [[-R, -R], [R, -R], [R, R], [-R, R], [0, -R], [R, 0], [0, R], [-R, 0]]) shaft.push(x, top, z, x, coreY - 6, z);
  const shaftMat = makeMat("line", "deep", ctx.theme, { near: 2, far: 120, opacity: 0.7 });
  world.add(new LineSegments(geo(shaft), shaftMat));
  mats.push(shaftMat);

  const plateMats: Mat[] = [];
  for (let k = 0; k < n; k++) {
    const y = plateY(k);
    const lines: number[] = [];
    for (let v = -R; v <= R + 0.01; v += 1.5) {
      if (Math.abs(v) < HOLE) {
        lines.push(-R, y, v, -HOLE, y, v, HOLE, y, v, R, y, v);
        lines.push(v, y, -R, v, y, -HOLE, v, y, HOLE, v, y, R);
      } else {
        lines.push(-R, y, v, R, y, v, v, y, -R, v, y, R);
      }
    }
    const h = HOLE;
    lines.push(-h, y, -h, h, y, -h, h, y, -h, h, y, h, h, y, h, -h, y, h, -h, y, h, -h, y, -h);
    const m = makeMat("line", "line", ctx.theme, { near: 1, far: 140, opacity: 0.5 });
    const plate = new LineSegments(geo(lines), m);
    world.add(plate);
    plateMats.push(m);
  }
  mats.push(...plateMats);

  // The locked core: the audio file (a small box) inside a cage of rings.
  const cageMat = makeMat("line", "bright", ctx.theme, { near: 1, far: 140, opacity: 0.8 });
  const cage: number[] = [];
  const RINGS = 6;
  for (let r = 0; r < RINGS; r++) {
    const a = (r / RINGS) * Math.PI;
    for (let i = 0; i < 48; i++) {
      const b0 = (i / 48) * Math.PI * 2;
      const b1 = ((i + 1) / 48) * Math.PI * 2;
      const p0 = [Math.cos(b0) * Math.cos(a) * 3.2, Math.sin(b0) * 3.2, Math.cos(b0) * Math.sin(a) * 3.2];
      const p1 = [Math.cos(b1) * Math.cos(a) * 3.2, Math.sin(b1) * 3.2, Math.cos(b1) * Math.sin(a) * 3.2];
      cage.push(...p0, ...p1);
    }
  }
  const cageObj = new LineSegments(geo(cage), cageMat);
  const fileMat = makeMat("line", "bright", ctx.theme, { near: 1, far: 140, opacity: 1 });
  const file = new LineSegments(geo(boxLines(1.4, 1.8, 0.4)), fileMat);
  file.position.y = -0.9;
  const core = new Group();
  core.add(cageObj, file);
  core.position.y = coreY;
  world.add(core);
  mats.push(cageMat, fileMat);
  scene.add(sky.group, world);
  world.traverse((o) => (o.frustumCulled = false));

  const st = { alt: CRUISE.alt, pitch: CRUISE.pitch, yaw: 0, z: 0 };
  let ready = false;
  // 0 while in the shaft, 1 once the camera is at the core below it.
  let past = 0;
  const DOWN = -1.5;
  return {
    kind: "vault",
    scene,
    camera: ctx.cam,
    mats,
    pose() {
      const vh = window.innerHeight;
      const box = section?.parentElement?.classList.contains("pin-spacer") ? section.parentElement : section;
      const r = box?.getBoundingClientRect();
      // How far the layers section is scrolled: below 0 before it pins, 0 to 1 while pinned, above 1 after.
      const lp = r ? -r.top / Math.max(1, r.height - vh) : 0;
      let want: { alt: number; pitch: number; yaw: number };
      if (lp < 0) {
        // Above ground: tilt the nose down while the hero scrolls away.
        const k = r ? Math.min(1, Math.max(0, 1 - r.top / vh)) : 0;
        want = { alt: CRUISE.alt + 2 * k, pitch: CRUISE.pitch + (DOWN - CRUISE.pitch) * smooth(0, 1, k), yaw: 0 };
      } else if (lp <= 1) {
        // In the shaft: plate k passes while layer card k is in front.
        const keys: Array<[number, number]> = [[0, CRUISE.alt + 2]];
        for (let k = 0; k < n; k++) keys.push([(k + 0.55) / n, plateY(k) - top + 1.2]);
        keys.push([1, plateY(n - 1) - top - 3]);
        want = { alt: track(lp, keys), pitch: DOWN, yaw: lp * 0.6 };
      } else {
        // At the bottom: hover above the core and circle it slowly while the rest of the page scrolls.
        want = { alt: coreY - top + 24, pitch: DOWN + 0.35, yaw: 0.6 + ctx.page * 2.4 };
      }
      const k = ready ? 0.1 : 1;
      ready = true;
      st.alt += (want.alt - st.alt) * k;
      st.pitch += (want.pitch - st.pitch) * k;
      st.yaw += (want.yaw - st.yaw) * k;
      // Circling the core: the camera moves on a ring around it, looking at its center.
      const orbit = lp > 1 ? 9 : 0;
      past += ((lp > 1 ? 1 : 0) - past) * 0.08;
      st.z += (orbit - st.z) * 0.05;
      return { alt: st.alt, pitch: st.pitch, roll: 0, yaw: st.yaw, runway: 0, ground: { x: Math.sin(st.yaw) * st.z, z: Math.cos(st.yaw) * st.z }, view: 40, wind: 1 - smooth(-0.3, 0, lp), exact: true };
    },
    update(_p, t, alpha) {
      world.position.set(-Math.sin(st.yaw) * st.z, 0, -Math.cos(st.yaw) * st.z);
      sky.update(t, alpha);
      shaftMat.uniforms.uOpacity.value = 0.7 * alpha * dim(ctx);
      const camY = -FLOOR_H + st.alt;
      plateMats.forEach((m, k) => {
        // The plate the camera is passing glows.
        const near = 1 - Math.min(1, Math.abs(camY - plateY(k)) / STEP);
        m.uniforms.uOpacity.value = (0.35 + 0.65 * near) * alpha;
      });
      core.rotation.y = t * 0.3;
      // Below the shaft the page shows long text, so the core steps back and dims to stay behind it.
      const calm = 1 - 0.55 * past;
      cageMat.uniforms.uOpacity.value = (0.6 + 0.2 * Math.sin(t * 2)) * alpha * calm;
      fileMat.uniforms.uOpacity.value = alpha * calm;
    },
    dispose() {
      scene.traverse((o) => (o as Mesh).geometry?.dispose());
      mats.forEach((m) => m.dispose());
    },
  };
}

// product: a gallery visit (design-system/SCENE-RULES.md, "Product: the gallery visit").
// The camera walks down a hall. At each painting it stops, turns to face it and steps closer until the
// painting fills the screen. The exhibits are real HTML hung on the walls with CSS 3D transforms that use
// the same camera as the scene (the method of three.js CSS3DRenderer), so every painting is always visible.
function makeGallery(ctx: Ctx, el: HTMLElement): Unit {
  const scene = new Scene();
  const mats: Mat[] = [];
  const section = el.querySelector<HTMLElement>("[data-exhibits]");
  const html = htmlStage(ctx, section);
  const exhibits = Array.from(el.querySelectorAll<HTMLElement>("[data-exhibit]"));
  const n = Math.max(1, exhibits.length);
  const floorY = -FLOOR_H;
  const EYE = 2.4;
  const HALF = 7;
  const WALL = HALF - 0.3;
  // Distance from the camera to a painting while looking at it.
  const D = 4;
  const GAP = 14;
  const CEIL = floorY + 9;
  // Painting i hangs on the left or right wall, starting on the reading side. The last one hangs on the end wall.
  const first = ctx.rtl ? 1 : -1;
  const sideOf = (i: number) => (i === n - 1 ? 0 : i % 2 === 0 ? first : -first);
  const zOf = (i: number) => -(i + 1) * GAP;
  const zEnd = zOf(n - 1) - D;
  const rnd = mulberry(83);
  const world = new Group();

  // Columns as clouds of points between the paintings, so the hall reads like a scan of a real gallery.
  const per = ctx.light ? 120 : 300;
  const cp: number[] = [];
  const cs: number[] = [];
  for (let z = GAP / 2; z > zEnd + 1; z -= GAP) {
    for (const side of [-1, 1]) {
      for (let i = 0; i < per; i++) {
        const a = rnd() * Math.PI * 2;
        const h = rnd() * (CEIL - floorY);
        const flare = h < 0.6 || h > CEIL - floorY - 0.8 ? 0.95 : 0.6;
        cp.push(side * (HALF + 0.6) + Math.cos(a) * flare, floorY + h, z + Math.sin(a) * flare);
        cs.push(0.5 + rnd() * 0.8);
      }
    }
  }
  const cg = new BufferGeometry();
  cg.setAttribute("position", new Float32BufferAttribute(cp, 3));
  cg.setAttribute("aColor", new Float32BufferAttribute(new Array(cp.length).fill(1), 3));
  cg.setAttribute("aShade", new Float32BufferAttribute(new Array(cp.length / 3).fill(1), 1));
  cg.setAttribute("aSize", new Float32BufferAttribute(cs, 1));
  const colMat = makeMat("point", "line", ctx.theme, { near: 1, far: 90, opacity: 0.8, size: 1.6, dpr: ctx.dpr });
  world.add(new Points(cg, colMat));

  // Grid ceiling, the foot of both walls and the end wall.
  const hall: number[] = [];
  for (let x = -HALF; x <= HALF + 0.01; x += HALF / 4) hall.push(x, CEIL, 20, x, CEIL, zEnd);
  for (let z = 20; z >= zEnd; z -= 3) hall.push(-HALF, CEIL, z, HALF, CEIL, z);
  for (const side of [-1, 1]) hall.push(side * HALF, floorY + 0.02, 20, side * HALF, floorY + 0.02, zEnd);
  for (let x = -HALF; x <= HALF + 0.01; x += 1) hall.push(x, floorY, zEnd - 0.05, x, CEIL, zEnd - 0.05);
  for (let y = floorY; y <= CEIL + 0.01; y += 1) hall.push(-HALF, y, zEnd - 0.05, HALF, y, zEnd - 0.05);
  const hallMat = makeMat("line", "deep", ctx.theme, { near: 2, far: 80, opacity: 0.6 });
  world.add(new LineSegments(geo(hall), hallMat));

  // Paintings. Each is a unit frame that is scaled every frame to match the HTML frame.
  // The outer frame sits just outside the canvas; faint lines inside are the empty canvas.
  const frameLines: number[] = [];
  const rect = (w: number, h: number) => [-w, -h, 0, w, -h, 0, w, -h, 0, w, h, 0, w, h, 0, -w, h, 0, -w, h, 0, -w, -h, 0];
  frameLines.push(...rect(0.52, 0.53), ...rect(0.506, 0.511));
  // The picture light: a short bar above the painting.
  frameLines.push(-0.18, 0.58, 0, 0.18, 0.58, 0);
  // dom: the point the HTML exhibit hangs from. Its scale turns CSS pixels into world units.
  type Painting = { holder: Group; canvas: Group; frame: Mat; dom: Object3D };
  const paintings: Painting[] = [];
  for (let i = 0; i < n; i++) {
    const side = sideOf(i);
    const holder = new Group();
    holder.position.set(side * WALL, floorY + EYE, side === 0 ? zEnd : zOf(i));
    holder.rotation.y = side === -1 ? Math.PI / 2 : side === 1 ? -Math.PI / 2 : 0;
    const canvas = new Group();
    const frame = makeMat("line", "bright", ctx.theme, { near: 1, far: 80, opacity: 0.5 });
    canvas.add(new LineSegments(geo(frameLines), frame));
    const dom = new Object3D();
    holder.add(canvas, dom);
    world.add(holder);
    paintings.push({ holder, canvas, frame, dom });
    mats.push(frame);
  }

  // Light on the floor under each painting.
  const lp: number[] = [];
  for (let i = 0; i < n; i++) {
    const side = sideOf(i);
    for (let k = 0; k < 70; k++) {
      const a = rnd() * Math.PI * 2;
      const r = Math.sqrt(rnd()) * 1.8;
      const x = side === 0 ? Math.cos(a) * r * 2 : side * (WALL - 1.6) + Math.cos(a) * r * 0.6;
      const z = side === 0 ? zEnd + 1.6 + Math.sin(a) * r * 0.6 : zOf(i) + Math.sin(a) * r * 1.6;
      lp.push(x, floorY + 0.05, z);
    }
  }
  const lg = new BufferGeometry();
  lg.setAttribute("position", new Float32BufferAttribute(lp, 3));
  lg.setAttribute("aColor", new Float32BufferAttribute(new Array(lp.length).fill(1), 3));
  lg.setAttribute("aShade", new Float32BufferAttribute(new Array(lp.length / 3).fill(1), 1));
  lg.setAttribute("aSize", new Float32BufferAttribute(new Array(lp.length / 3).fill(1.4), 1));
  const lightMat = makeMat("point", "bright", ctx.theme, { near: 1, far: 60, opacity: 0.45, size: 2, dpr: ctx.dpr });
  world.add(new Points(lg, lightMat));
  mats.push(colMat, hallMat, lightMat);
  scene.add(world);
  world.traverse((o) => (o.frustumCulled = false));

  const st = { x: 0, z: 10, yaw: 0, sway: 1 };
  let ready = false;
  // Which painting is in front.
  let active = -1;
  return {
    kind: "gallery",
    scene,
    camera: ctx.cam,
    mats,
    pose() {
      const vh = window.innerHeight;
      const r = section?.getBoundingClientRect();
      const lp = section ? sceneBus.get(section) : 0;
      let want = { x: 0, z: 10, yaw: 0, sway: 1 };
      active = -1;
      if (r && lp <= 0 && r.top > 0) {
        // Entrance: walk in through the door while the hero scrolls away.
        const k = Math.min(1, Math.max(0, 1 - r.top / vh));
        want = { x: 0, z: 10 * (1 - k), yaw: 0, sway: 1 };
      } else if (lp < 1) {
        const i = Math.min(n - 1, Math.floor(lp * n));
        const f = lp * n - i;
        const side = sideOf(i);
        const last = i === n - 1;
        const zPrev = i === 0 ? 0 : zOf(i - 1);
        const walk = smooth(0, EXHIBIT_ARRIVE, f);
        const face = smooth(EXHIBIT_ARRIVE, EXHIBIT_FACE, f);
        const back = last ? 0 : smooth(EXHIBIT_LEAVE, 1, f);
        const look = face * (1 - back);
        const stepBack = last ? 5 * smooth(0.9, 1, f) : 0;
        want = {
          x: side * (WALL - D) * look,
          z: zPrev + (zOf(i) - zPrev) * walk + stepBack,
          yaw: (side === -1 ? Math.PI / 2 : side === 1 ? -Math.PI / 2 : 0) * look,
          sway: 1 - look * (1 - smooth(0.9, 1, last ? f : 0)),
        };
        active = look > 0.5 ? i : -1;
      } else {
        // Exit: stand back from the last painting while the call to action shows.
        want = { x: 0, z: zOf(n - 1) + 5, yaw: 0, sway: 1 };
      }
      const k = ready ? 0.14 : 1;
      ready = true;
      st.x += (want.x - st.x) * k;
      st.z += (want.z - st.z) * k;
      st.yaw += (want.yaw - st.yaw) * k;
      st.sway += (want.sway - st.sway) * k;
      return { alt: EYE, pitch: 0, roll: 0, yaw: st.yaw, runway: 0, ground: { x: st.x, z: st.z }, wind: 0.3, sway: st.sway, exact: true };
    },
    update(_p, t, alpha) {
      world.position.set(-st.x, 0, -st.z);
      // A painting seen from distance D covers the same part of the screen as the exhibit's CSS size.
      const px = html.px(D);
      world.updateMatrixWorld(true);
      html.begin(alpha);
      paintings.forEach((pt, i) => {
        const ex = exhibits[i];
        const w = ex ? ex.offsetWidth : window.innerWidth * 0.8;
        const h = ex ? ex.offsetHeight : window.innerHeight * 0.7;
        pt.canvas.scale.set(w * px, h * px, 1);
        pt.dom.scale.set(px, px, px);
        pt.dom.updateMatrixWorld();
        const on = i === active ? 1 : 0;
        pt.frame.uniforms.uOpacity.value = (0.45 + 0.55 * on) * (0.85 + 0.15 * Math.sin(t * 1.4 + i)) * alpha;
        if (ex) html.place(ex, pt.dom);
      });
      colMat.uniforms.uOpacity.value = 0.8 * alpha;
      hallMat.uniforms.uOpacity.value = 0.6 * alpha * dim(ctx);
      lightMat.uniforms.uOpacity.value = 0.45 * alpha;
    },
    dispose() {
      html.dispose();
      scene.traverse((o) => (o as Mesh).geometry?.dispose());
      mats.forEach((m) => m.dispose());
    },
  };
}

// A glowing frame around a piece of hung HTML: a unit rectangle, scaled every frame to the HTML's size.
function panelFrame(ctx: Ctx) {
  const r = (w: number, h: number) => [-w, -h, 0, w, -h, 0, w, -h, 0, w, h, 0, w, h, 0, -w, h, 0, -w, h, 0, -w, -h, 0];
  const mat = makeMat("line", "bright", ctx.theme, { near: 1, far: 120, opacity: 0.5 });
  const lines = new LineSegments(geo([...r(0.51, 0.51), ...r(0.525, 0.525)]), mat);
  return { lines, mat };
}

// A circle of line segments in the XZ plane.
function circle(radius: number, y = 0, seg = 96) {
  const out: number[] = [];
  for (let i = 0; i < seg; i++) {
    const a0 = (i / seg) * Math.PI * 2;
    const a1 = ((i + 1) / seg) * Math.PI * 2;
    out.push(Math.cos(a0) * radius, y, Math.sin(a0) * radius, Math.cos(a1) * radius, y, Math.sin(a1) * radius);
  }
  return out;
}

// solutions: the roles stand in a ring around a glowing core. Scrolling turns the ring, so each role comes to
// the front in turn. Beams join the core to each role; the role in front gets the bright one.
function makeOrbit(ctx: Ctx, el: HTMLElement): Unit {
  const scene = new Scene();
  const mats: Mat[] = [];
  const section = el.querySelector<HTMLElement>("[data-roles]");
  const html = htmlStage(ctx, section);
  const items = Array.from(el.querySelectorAll<HTMLElement>("[data-role]"));
  const n = Math.max(1, items.length);
  const floorY = -FLOOR_H;
  const EYE = 2.4;
  const R = 9;
  const D = 7;
  const step = (Math.PI * 2) / n;
  // The next role comes from the reading direction.
  const dir = ctx.rtl ? -1 : 1;
  const sky = buildSky(ctx, 97);
  mats.push(...sky.mats);
  const ring = new Group();
  ring.position.set(0, floorY + EYE - 0.4, -(R + D));

  // The core: a sphere of points and three tilted orbits around it.
  const rnd = mulberry(91);
  const CN = ctx.light ? 220 : 520;
  const cp: number[] = [];
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < CN; i++) {
    const y = 1 - (2 * (i + 0.5)) / CN;
    const r = Math.sqrt(1 - y * y);
    const k = 1.8 * (0.85 + 0.15 * rnd());
    cp.push(Math.cos(i * golden) * r * k, y * k, Math.sin(i * golden) * r * k);
  }
  const cg = new BufferGeometry();
  cg.setAttribute("position", new Float32BufferAttribute(cp, 3));
  cg.setAttribute("aColor", new Float32BufferAttribute(new Array(cp.length).fill(1), 3));
  cg.setAttribute("aShade", new Float32BufferAttribute(new Array(CN).fill(1), 1));
  cg.setAttribute("aSize", new Float32BufferAttribute(new Array(CN).fill(1.2), 1));
  const coreMat = makeMat("point", "bright", ctx.theme, { near: 1, far: 120, opacity: 0.9, size: 2.2, dpr: ctx.dpr });
  const core = new Group();
  core.add(new Points(cg, coreMat));
  const orbitMat = makeMat("line", "line", ctx.theme, { near: 1, far: 120, opacity: 0.6 });
  const orbits: Group[] = [];
  for (let k = 0; k < 3; k++) {
    const g = new Group();
    g.add(new LineSegments(geo(circle(3 + k * 0.6)), orbitMat));
    g.rotation.set(0.5 + k * 0.7, k * 1.1, 0.3 * k);
    core.add(g);
    orbits.push(g);
  }
  ring.add(core);
  mats.push(coreMat, orbitMat);

  // The track on the floor under the roles.
  const trackMat = makeMat("line", "line", ctx.theme, { near: 1, far: 120, opacity: 0.6 });
  ring.add(new LineSegments(geo([...circle(R, -EYE + 0.43), ...circle(R * 0.55, -EYE + 0.43, 64)]), trackMat));
  mats.push(trackMat);

  type Seat = { holder: Group; frame: ReturnType<typeof panelFrame>; dom: Object3D; beam: Mat };
  const seats: Seat[] = [];
  for (let i = 0; i < n; i++) {
    const a = dir * i * step;
    const holder = new Group();
    holder.position.set(Math.sin(a) * R, 0, Math.cos(a) * R);
    holder.rotation.y = a;
    const frame = panelFrame(ctx);
    const dom = new Object3D();
    holder.add(frame.lines, dom);
    ring.add(holder);
    const beam = makeMat("line", "bright", ctx.theme, { near: 1, far: 120, opacity: 0.2 });
    ring.add(new LineSegments(geo([0, 0, 0, Math.sin(a) * (R - 0.2), 0, Math.cos(a) * (R - 0.2)]), beam));
    seats.push({ holder, frame, dom, beam });
    mats.push(frame.mat, beam);
  }
  scene.add(sky.group, ring);
  scene.traverse((o) => (o.frustumCulled = false));

  const st = { z: 16, alt: EYE + 5, pitch: -0.28, sway: 1, turn: 0 };
  let ready = false;
  let active = 0;
  return {
    kind: "orbit",
    scene,
    camera: ctx.cam,
    mats,
    pose() {
      const vh = window.innerHeight;
      const r = section?.getBoundingClientRect();
      const lp = section ? sceneBus.get(section) : 0;
      // Stop at each role: turn between them, hold while it is in front.
      const f = lp * (n - 1);
      const i = Math.min(n - 1, Math.floor(f));
      const turn = i + smooth(0.35, 0.75, f - i);
      let want = { z: 0, alt: EYE, pitch: 0, sway: 0.35 };
      if (r && lp <= 0 && r.top > 0) {
        // While the hero scrolls away: come down from above and walk up to the ring.
        const k = Math.min(1, Math.max(0, 1 - r.top / vh));
        want = { z: 16 * (1 - k), alt: EYE + 5 * (1 - k), pitch: -0.28 * (1 - k), sway: 1 };
      }
      const k = ready ? 0.1 : 1;
      ready = true;
      st.z += (want.z - st.z) * k;
      st.alt += (want.alt - st.alt) * k;
      st.pitch += (want.pitch - st.pitch) * k;
      st.sway += (want.sway - st.sway) * k;
      st.turn += (turn - st.turn) * k;
      active = Math.round(st.turn);
      return { alt: st.alt, pitch: st.pitch, roll: 0, yaw: 0, runway: 0, ground: { x: 0, z: -st.z }, wind: 0.2, sway: st.sway, exact: true };
    },
    update(_p, t, alpha) {
      ring.position.z = -(R + D) + st.z;
      ring.rotation.y = -dir * st.turn * step;
      core.rotation.y = t * 0.2;
      orbits.forEach((g, k) => (g.rotation.z += 0.002 * (k + 1)));
      sky.update(t, alpha);
      const px = html.px(D);
      scene.updateMatrixWorld(true);
      html.begin(alpha);
      seats.forEach((st2, i) => {
        const item = items[i];
        const w = item ? item.offsetWidth : 600;
        const h = item ? item.offsetHeight : 400;
        st2.frame.lines.scale.set(w * px, h * px, 1);
        st2.dom.scale.set(px, px, px);
        st2.dom.updateMatrixWorld();
        const on = i === active ? 1 : 0;
        st2.frame.mat.uniforms.uOpacity.value = (0.35 + 0.65 * on) * alpha;
        st2.beam.uniforms.uOpacity.value = (0.15 + 0.75 * on * (0.75 + 0.25 * Math.sin(t * 3))) * alpha;
        if (item) html.place(item, st2.dom, 60);
      });
      coreMat.uniforms.uOpacity.value = (0.75 + 0.2 * Math.sin(t * 2)) * alpha;
      orbitMat.uniforms.uOpacity.value = 0.6 * alpha;
      trackMat.uniforms.uOpacity.value = 0.6 * alpha * dim(ctx);
    },
    dispose() {
      html.dispose();
      scene.traverse((o) => (o as Mesh).geometry?.dispose());
      mats.forEach((m) => m.dispose());
    },
  };
}

// pricing: the camera rides an open lift straight up. One plan waits on each floor; the lift stops there.
// The grid falls away below while the camera climbs.
function makeLift(ctx: Ctx, el: HTMLElement): Unit {
  const scene = new Scene();
  const mats: Mat[] = [];
  const section = el.querySelector<HTMLElement>("[data-floors]");
  const html = htmlStage(ctx, section);
  const items = Array.from(el.querySelectorAll<HTMLElement>("[data-plan]"));
  const n = Math.max(1, items.length);
  const floorY = -FLOOR_H;
  const EYE = 2.4;
  const D = 6;
  const LEVEL = 10;
  const FIRST = 7;
  const levelAlt = (i: number) => FIRST + i * LEVEL;
  const top = levelAlt(n - 1) + 9;
  const W = 4.4;
  const zFront = 1.5;
  const zBack = -D - 1.5;
  const sky = buildSky(ctx, 103);
  mats.push(...sky.mats);
  const world = new Group();

  // The open lift shaft: four posts and a ring of lines every 2.5 units.
  const cage: number[] = [];
  for (const [x, z] of [[-W, zFront], [W, zFront], [W, zBack], [-W, zBack]]) cage.push(x, floorY, z, x, floorY + top, z);
  for (let y = floorY + 2.5; y <= floorY + top; y += 2.5) {
    cage.push(-W, y, zFront, -W, y, zBack, W, y, zFront, W, y, zBack, -W, y, zBack, W, y, zBack);
  }
  const cageMat = makeMat("line", "deep", ctx.theme, { near: 1, far: 70, opacity: 0.7 });
  world.add(new LineSegments(geo(cage), cageMat));
  mats.push(cageMat);

  type Floor = { slab: Mat; frame: ReturnType<typeof panelFrame>; dom: Object3D };
  const floors: Floor[] = [];
  for (let i = 0; i < n; i++) {
    const y = floorY + levelAlt(i);
    // The floor slab under each plan: an outline with a grid inside.
    const slab: number[] = [];
    const sy = y - 4;
    for (let x = -W; x <= W + 0.01; x += W / 4) slab.push(x, sy, zFront, x, sy, zBack);
    for (let z = zFront; z >= zBack - 0.01; z -= (zFront - zBack) / 6) slab.push(-W, sy, z, W, sy, z);
    const slabMat = makeMat("line", "bright", ctx.theme, { near: 1, far: 80, opacity: 0.3 });
    world.add(new LineSegments(geo(slab), slabMat));
    const holder = new Group();
    holder.position.set(0, y, -D);
    const frame = panelFrame(ctx);
    const dom = new Object3D();
    holder.add(frame.lines, dom);
    world.add(holder);
    floors.push({ slab: slabMat, frame, dom });
    mats.push(slabMat, frame.mat);
  }
  scene.add(sky.group, world);
  scene.traverse((o) => (o.frustumCulled = false));

  const st = { z: 8, alt: EYE, pitch: 0.38, sway: 1 };
  let ready = false;
  let active = -1;
  return {
    kind: "lift",
    scene,
    camera: ctx.cam,
    mats,
    pose() {
      const vh = window.innerHeight;
      const r = section?.getBoundingClientRect();
      const lp = section ? sceneBus.get(section) : 0;
      let want = { z: 0, alt: EYE, pitch: 0, sway: 1 };
      active = -1;
      if (r && lp <= 0 && r.top > 0) {
        // While the hero scrolls away: stand in front of the lift and look up the shaft, then step in.
        const k = Math.min(1, Math.max(0, 1 - r.top / vh));
        want = { z: 8 * (1 - k), alt: EYE, pitch: 0.38 * (1 - k) + 0.1 * k, sway: 1 };
      } else {
        const f = Math.min(1, lp) * n;
        const i = Math.min(n - 1, Math.floor(f));
        const frac = lp >= 1 ? 1 : f - i;
        const from = i === 0 ? EYE : levelAlt(i - 1);
        const rise = smooth(0, 0.45, frac);
        const stop = smooth(0.4, 0.5, frac);
        want = { z: 0, alt: from + (levelAlt(i) - from) * rise, pitch: 0.18 * Math.sin(Math.PI * rise) * (1 - stop), sway: 1 - 0.7 * stop };
        if (stop > 0.5) active = i;
      }
      const k = ready ? 0.1 : 1;
      ready = true;
      st.z += (want.z - st.z) * k;
      st.alt += (want.alt - st.alt) * k;
      st.pitch += (want.pitch - st.pitch) * k;
      st.sway += (want.sway - st.sway) * k;
      return { alt: st.alt, pitch: st.pitch, roll: 0, yaw: 0, runway: 0, ground: { x: 0, z: -st.z }, view: st.alt, wind: 0, sway: st.sway, exact: true };
    },
    update(_p, t, alpha) {
      world.position.z = st.z;
      sky.update(t, alpha);
      const px = html.px(D);
      scene.updateMatrixWorld(true);
      html.begin(alpha);
      floors.forEach((fl, i) => {
        const item = items[i];
        const w = item ? item.offsetWidth : 480;
        const h = item ? item.offsetHeight : 400;
        fl.frame.lines.scale.set(w * px, h * px, 1);
        fl.dom.scale.set(px, px, px);
        fl.dom.updateMatrixWorld();
        const on = i === active ? 1 : 0;
        fl.frame.mat.uniforms.uOpacity.value = (0.35 + 0.65 * on) * alpha;
        fl.slab.uniforms.uOpacity.value = (0.25 + 0.5 * on) * alpha;
        if (item) html.place(item, fl.dom, 60);
      });
      cageMat.uniforms.uOpacity.value = 0.7 * alpha * dim(ctx);
    },
    dispose() {
      html.dispose();
      scene.traverse((o) => (o as Mesh).geometry?.dispose());
      mats.forEach((m) => m.dispose());
    },
  };
}

// contact: one bright beacon stands on the grid ahead, beside the form. The camera drifts slowly toward it,
// a little with time and more with the scroll. Light rises along the beacon and rings pulse out from its foot.
function makeBeacon(ctx: Ctx): Unit {
  const scene = new Scene();
  const mats: Mat[] = [];
  const floorY = -FLOOR_H;
  const EYE = 2.4;
  // Far away in the gap between the form and the side column, so it never sits behind text.
  const bx = ctx.rtl ? -3 : 3;
  const bz = -62;
  const H = 60;
  const sky = buildSky(ctx, 113);
  mats.push(...sky.mats);
  const world = new Group();
  const beacon = new Group();
  beacon.position.set(bx, floorY, bz);
  world.add(beacon);

  // The pillar: a bright line in the middle and a thin cylinder of lines around it.
  const pillar: number[] = [0, 0, 0, 0, H, 0];
  for (let k = 0; k < 8; k++) {
    const a = (k / 8) * Math.PI * 2;
    pillar.push(Math.cos(a) * 0.35, 0, Math.sin(a) * 0.35, Math.cos(a) * 0.35, H, Math.sin(a) * 0.35);
  }
  const pillarMat = makeMat("line", "bright", ctx.theme, { near: 1, far: 140, opacity: 0.8 });
  beacon.add(new LineSegments(geo(pillar), pillarMat));

  // Light rising along the pillar.
  const rnd = mulberry(127);
  const PN = ctx.light ? 120 : 280;
  const base: number[] = [];
  for (let i = 0; i < PN; i++) base.push(rnd() * Math.PI * 2, 0.15 + rnd() * 1.1, rnd() * H, 2 + rnd() * 5);
  const pp = new Float32Array(PN * 3);
  const pg = new BufferGeometry();
  pg.setAttribute("position", new BufferAttribute(pp, 3));
  pg.setAttribute("aColor", new Float32BufferAttribute(new Array(PN * 3).fill(1), 3));
  pg.setAttribute("aShade", new Float32BufferAttribute(new Array(PN).fill(1), 1));
  pg.setAttribute("aSize", new Float32BufferAttribute(new Array(PN).fill(1.3), 1));
  const riseMat = makeMat("point", "bright", ctx.theme, { near: 1, far: 140, opacity: 0.9, size: 2.4, dpr: ctx.dpr });
  beacon.add(new Points(pg, riseMat));

  // Rings that pulse out from the foot of the beacon.
  const rings: Array<{ obj: LineSegments; mat: Mat }> = [];
  for (let k = 0; k < 4; k++) {
    const mat = makeMat("line", "bright", ctx.theme, { near: 1, far: 140, opacity: 0.5 });
    const obj = new LineSegments(geo(circle(1, 0.05)), mat);
    beacon.add(obj);
    rings.push({ obj, mat });
    mats.push(mat);
  }
  mats.push(pillarMat, riseMat);
  scene.add(sky.group, world);
  scene.traverse((o) => (o.frustumCulled = false));

  let z = 0;
  let time = 0;
  let ready = false;
  return {
    kind: "beacon",
    scene,
    camera: ctx.cam,
    mats,
    pose() {
      const want = -(ctx.page * 12 + 6 * (1 - Math.exp(-time / 50)));
      z = ready ? z + (want - z) * 0.05 : want;
      ready = true;
      return { alt: EYE, pitch: 0.05, roll: 0, yaw: 0, runway: 0, ground: { x: 0, z }, wind: 0.25, exact: true };
    },
    update(_p, t, alpha) {
      time = t;
      world.position.z = -z;
      sky.update(t, alpha);
      for (let i = 0; i < PN; i++) {
        const a = base[i * 4];
        const r = base[i * 4 + 1];
        const y = (base[i * 4 + 2] + t * base[i * 4 + 3]) % H;
        pp[i * 3] = Math.cos(a + t * 0.3) * r;
        pp[i * 3 + 1] = y;
        pp[i * 3 + 2] = Math.sin(a + t * 0.3) * r;
      }
      pg.attributes.position.needsUpdate = true;
      rings.forEach((rg, k) => {
        const f = ((t * 0.25 + k / rings.length) % 1);
        const radius = 1 + f * 14;
        rg.obj.scale.set(radius, 1, radius);
        rg.mat.uniforms.uOpacity.value = 0.6 * (1 - f) * alpha;
      });
      pillarMat.uniforms.uOpacity.value = (0.7 + 0.2 * Math.sin(t * 2.2)) * alpha;
      riseMat.uniforms.uOpacity.value = 0.9 * alpha;
    },
    dispose() {
      scene.traverse((o) => (o as Mesh).geometry?.dispose());
      mats.forEach((m) => m.dispose());
    },
  };
}

// about: the visitor lies back and looks up at the night sky. Each principle is a bright star with its text
// beside it. Scrolling pans the view from star to star and draws the constellation line between them.
// After the last star the view comes back down to the horizon.
function makeDome(ctx: Ctx, el: HTMLElement): Unit {
  const scene = new Scene();
  const mats: Mat[] = [];
  const section = el.querySelector<HTMLElement>("[data-stars]");
  const html = htmlStage(ctx, section);
  const items = Array.from(el.querySelectorAll<HTMLElement>("[data-star]"));
  const n = Math.max(1, items.length);
  const floorY = -FLOOR_H;
  const EYE = 2.4;
  const DIST = 40;
  const dir = ctx.rtl ? -1 : 1;
  // Where each star sits in the sky: spread across, alternating high and low.
  const yawOf = (i: number) => -dir * (i - (n - 1) / 2) * 0.55;
  const elOf = (i: number) => (i % 2 === 0 ? 0.42 : 0.78);
  const at = (yaw: number, el: number, out = new Vector3()) =>
    out.set(-Math.sin(yaw) * Math.cos(el) * DIST, floorY + EYE + Math.sin(el) * DIST, -Math.cos(yaw) * Math.cos(el) * DIST);
  const sky = buildSky(ctx, 131);
  mats.push(...sky.mats);

  const starPos = Array.from({ length: n }, (_, i) => at(yawOf(i), elOf(i)));
  const sp: number[] = [];
  starPos.forEach((v) => sp.push(v.x, v.y, v.z));
  const sg = new BufferGeometry();
  sg.setAttribute("position", new Float32BufferAttribute(sp, 3));
  sg.setAttribute("aColor", new Float32BufferAttribute(new Array(sp.length).fill(1), 3));
  sg.setAttribute("aShade", new Float32BufferAttribute(new Array(n).fill(1), 1));
  const sizes = new Float32Array(n).fill(3);
  sg.setAttribute("aSize", new BufferAttribute(sizes, 1));
  const starMat = makeMat("point", "bright", ctx.theme, { near: 1, far: 200, opacity: 1, size: 4.5, dpr: ctx.dpr });
  const haloMat = makeMat("point", "line", ctx.theme, { near: 1, far: 200, opacity: 0.35, size: 16, dpr: ctx.dpr });
  scene.add(new Points(sg, haloMat), new Points(sg, starMat));

  // The constellation line, drawn from star to star with the scroll.
  const lp = new Float32Array(Math.max(1, n - 1) * 6);
  const lg = new BufferGeometry();
  lg.setAttribute("position", new BufferAttribute(lp, 3));
  lg.setAttribute("aColor", new Float32BufferAttribute(new Array(lp.length).fill(1), 3));
  lg.setAttribute("aShade", new Float32BufferAttribute(new Array(lp.length / 3).fill(1), 1));
  const lineMat = makeMat("line", "bright", ctx.theme, { near: 1, far: 200, opacity: 0.85 });
  scene.add(new LineSegments(lg, lineMat));
  mats.push(starMat, haloMat, lineMat);

  // The text of each principle hangs just below its star and faces the viewer.
  const doms = Array.from({ length: n }, (_, i) => {
    const o = new Object3D();
    o.position.copy(at(yawOf(i), elOf(i) - 0.17));
    o.lookAt(0, floorY + EYE, 0);
    scene.add(o);
    return o;
  });
  scene.add(sky.group);
  scene.traverse((o) => (o.frustumCulled = false));

  const st = { yaw: 0, pitch: 0.02, drawn: 0 };
  let ready = false;
  const tmp = new Vector3();
  const forward = new Vector3();
  return {
    kind: "dome",
    scene,
    camera: ctx.cam,
    mats,
    pose() {
      const vh = window.innerHeight;
      const r = section?.getBoundingClientRect();
      const lpos = section ? sceneBus.get(section) : 0;
      // Pan from star to star, holding at each one.
      const f = lpos * (n - 1);
      const i = Math.min(n - 1, Math.floor(f));
      const drawn = i + smooth(0.3, 0.7, f - i);
      const j = Math.min(n - 1, Math.floor(drawn));
      const s = drawn - j;
      const next = Math.min(n - 1, j + 1);
      let want = { yaw: yawOf(j) + (yawOf(next) - yawOf(j)) * s, pitch: elOf(j) + (elOf(next) - elOf(j)) * s - 0.06 };
      if (r && lpos <= 0 && r.top > 0) {
        // While the hero scrolls away: look up from the horizon to the first star.
        const k = smooth(0, 1, 1 - r.top / vh);
        want = { yaw: yawOf(0) * k, pitch: 0.02 + (elOf(0) - 0.08) * k };
      } else if (r && r.bottom < vh) {
        // After the last star: come back down to the horizon for the rest of the page.
        const k = smooth(0, 1, 1 - r.bottom / vh);
        want = { yaw: want.yaw * (1 - k), pitch: want.pitch + (0.02 - want.pitch) * k };
      }
      const k = ready ? 0.08 : 1;
      ready = true;
      st.yaw += (want.yaw - st.yaw) * k;
      st.pitch += (want.pitch - st.pitch) * k;
      st.drawn += ((lpos <= 0 ? 0 : drawn) - st.drawn) * k;
      return { alt: EYE, pitch: st.pitch, roll: 0, yaw: st.yaw, runway: 0, wind: 0, sway: 0.5, exact: true };
    },
    update(_p, t, alpha) {
      sky.update(t, alpha);
      // Draw the line up to where the scroll has reached.
      for (let k = 0; k < n - 1; k++) {
        const a = starPos[k];
        const b = starPos[k + 1];
        const part = Math.min(1, Math.max(0, st.drawn - k));
        tmp.copy(a).lerp(b, part);
        lp.set([a.x, a.y, a.z, tmp.x, tmp.y, tmp.z], k * 6);
      }
      lg.attributes.position.needsUpdate = true;
      const active = Math.round(st.drawn);
      for (let k = 0; k < n; k++) sizes[k] = k === active ? 4.2 + 0.8 * Math.sin(t * 3) : k <= st.drawn + 0.5 ? 3.2 : 2.2;
      sg.attributes.aSize.needsUpdate = true;
      starMat.uniforms.uOpacity.value = alpha;
      haloMat.uniforms.uOpacity.value = 0.35 * alpha;
      lineMat.uniforms.uOpacity.value = 0.85 * alpha;
      const px = html.px(DIST);
      html.begin(alpha);
      ctx.cam.getWorldDirection(forward);
      doms.forEach((o, k) => {
        o.scale.set(px, px, px);
        o.updateMatrixWorld();
        // Looking up, text near the edge of the view is tilted by perspective. Dim it, so the star in
        // front stands out and stays easy to read.
        tmp.setFromMatrixPosition(o.matrixWorld).sub(ctx.cam.position).normalize();
        const off = Math.acos(Math.min(1, Math.max(-1, tmp.dot(forward))));
        const item = items[k];
        if (item) html.place(item, o, 200, 1 - 0.8 * smooth(0.3, 0.65, off));
      });
    },
    dispose() {
      html.dispose();
      scene.traverse((o) => (o as Mesh).geometry?.dispose());
      mats.forEach((m) => m.dispose());
    },
  };
}

// roi: two towers of hours stand on the grid, today (H1) and with Homayar (H2). The dotted part above the shorter
// tower is the saved time (S). The calculator writes H1 and H2 on [data-roi]; the towers follow the inputs.
// The camera walks up to the towers, rises to the level where they differ, steps sideways around the gap and backs off.
function makeStacks(ctx: Ctx, el: HTMLElement): Unit {
  const scene = new Scene();
  const mats: Mat[] = [];
  // Looked up every frame: the engine keeps this unit across page visits, so a stored element can go stale.
  const calc = () => (el.isConnected ? el : document.querySelector<HTMLElement>("[data-scene='stacks']"))?.querySelector<HTMLElement>("[data-roi]");
  const floorY = -FLOOR_H;
  const EYE = 2.4;
  // The taller tower is MAXH units high once the hours reach REF (the example H1). Below that the towers are shorter.
  const MAXH = 12;
  const REF = 160;
  const STEP = 0.5;
  const HALF = 0.9;
  const PLATES = Math.round(MAXH / STEP);
  // The towers stand on the inline-end side of the screen (right in English, left in Persian), away from the text.
  // On phones they stand in the middle, farther away and dimmer.
  const side = ctx.rtl ? -1 : 1;
  const phone = ctx.light;
  const cx = phone ? 0 : side * 6;
  const cz = phone ? -36 : -30;
  const sky = buildSky(ctx, 149);
  mats.push(...sky.mats);
  const world = new Group();

  const ring = (y: number, r = HALF) => [-r, y, -r, r, y, -r, r, y, -r, r, y, r, r, y, r, -r, y, r, -r, y, r, -r, y, -r];
  const tower = (x: number, role: Role, seed: number) => {
    const group = new Group();
    group.position.set(x, floorY, cz);
    // Four edges of unit height, scaled to the tower height every frame.
    const e: number[] = [];
    for (const [a, b] of [[-HALF, -HALF], [HALF, -HALF], [HALF, HALF], [-HALF, HALF]]) e.push(a, 0, b, a, 1, b);
    const edgeMat = makeMat("line", role, ctx.theme, { near: 1, far: 120, opacity: 0.8 });
    const edges = new LineSegments(geo(e), edgeMat);
    const baseMat = makeMat("line", role, ctx.theme, { near: 1, far: 120, opacity: 0.7 });
    const base = new LineSegments(geo([...ring(0.02), ...ring(0.02, HALF * 1.6)]), baseMat);
    // A glowing plate every STEP units. Only the plates below the top are drawn.
    const pp: number[] = [];
    for (let i = 1; i <= PLATES; i++) pp.push(...ring(i * STEP));
    const plateGeo = geo(pp);
    const plateMat = makeMat("line", role, ctx.theme, { near: 1, far: 120, opacity: 0.45 });
    const topMat = makeMat("line", "bright", ctx.theme, { near: 1, far: 120, opacity: 0.95 });
    const top = new LineSegments(geo(ring(0)), topMat);
    // Light rising inside the tower.
    const rnd = mulberry(seed);
    const PN = phone ? 40 : 90;
    const seeds: number[] = [];
    for (let i = 0; i < PN; i++) seeds.push((rnd() * 2 - 1) * HALF * 0.85, (rnd() * 2 - 1) * HALF * 0.85, rnd(), 0.04 + rnd() * 0.1);
    const pts = new Float32Array(PN * 3);
    const ptGeo = new BufferGeometry();
    ptGeo.setAttribute("position", new BufferAttribute(pts, 3));
    ptGeo.setAttribute("aColor", new Float32BufferAttribute(new Array(PN * 3).fill(1), 3));
    ptGeo.setAttribute("aShade", new Float32BufferAttribute(new Array(PN).fill(1), 1));
    ptGeo.setAttribute("aSize", new Float32BufferAttribute(new Array(PN).fill(1.2), 1));
    const ptMat = makeMat("point", "bright", ctx.theme, { near: 1, far: 120, opacity: 0.8, size: 2.2, dpr: ctx.dpr });
    group.add(base, edges, new LineSegments(plateGeo, plateMat), top, new Points(ptGeo, ptMat));
    world.add(group);
    mats.push(edgeMat, baseMat, plateMat, topMat, ptMat);
    return {
      update(h: number, t: number, a: number) {
        edges.scale.y = Math.max(0.001, h);
        top.position.y = h;
        plateGeo.setDrawRange(0, Math.min(PLATES, Math.floor(h / STEP + 1e-6)) * 8);
        for (let i = 0; i < PN; i++) {
          pts[i * 3] = seeds[i * 4];
          pts[i * 3 + 1] = (((seeds[i * 4 + 2] + t * seeds[i * 4 + 3]) % 1) * h);
          pts[i * 3 + 2] = seeds[i * 4 + 1];
        }
        ptGeo.attributes.position.needsUpdate = true;
        edgeMat.uniforms.uOpacity.value = 0.8 * a;
        baseMat.uniforms.uOpacity.value = 0.7 * a;
        plateMat.uniforms.uOpacity.value = 0.45 * a;
        topMat.uniforms.uOpacity.value = (h > 0.05 ? 0.95 : 0) * a;
        ptMat.uniforms.uOpacity.value = (h > 0.05 ? 0.8 : 0) * a;
      },
    };
  };
  // "Today" stands on the inline-start side of the pair, like the first line of text.
  const xA = cx - side * 1.6;
  const xB = cx + side * 1.6;
  const today = tower(xA, "line", 151);
  const homayar = tower(xB, "bright", 157);

  // The saved time: a dotted outline above the "with Homayar" tower, up to the height of "today".
  const GN = 200;
  const gp = new Float32Array(GN * 3);
  const gGeo = new BufferGeometry();
  gGeo.setAttribute("position", new BufferAttribute(gp, 3));
  gGeo.setAttribute("aColor", new Float32BufferAttribute(new Array(GN * 3).fill(1), 3));
  gGeo.setAttribute("aShade", new Float32BufferAttribute(new Array(GN).fill(1), 1));
  gGeo.setAttribute("aSize", new Float32BufferAttribute(new Array(GN).fill(1.4), 1));
  const gapMat = makeMat("point", "bright", ctx.theme, { near: 1, far: 120, opacity: 0.9, size: 2.4, dpr: ctx.dpr });
  const gapPts = new Points(gGeo, gapMat);
  gapPts.position.set(xB, floorY, cz);
  // A bracket just outside the "with Homayar" tower marks the size of the gap, with a tick to the tower at each end.
  const lp = new Float32Array(3 * 6);
  const lGeo = new BufferGeometry();
  lGeo.setAttribute("position", new BufferAttribute(lp, 3));
  lGeo.setAttribute("aColor", new Float32BufferAttribute(new Array(lp.length).fill(1), 3));
  lGeo.setAttribute("aShade", new Float32BufferAttribute(new Array(lp.length / 3).fill(1), 1));
  const levelMat = makeMat("line", "bright", ctx.theme, { near: 1, far: 120, opacity: 0.9 });
  const levels = new LineSegments(lGeo, levelMat);
  world.add(gapPts, levels);
  mats.push(gapMat, levelMat);
  scene.add(sky.group, world);
  scene.traverse((o) => (o.frustumCulled = false));

  const st = { hA: 0, hB: 0, on: 1, d: 34, theta: 0, rise: 0, x: 0, z: 0, alt: EYE };
  let ready = false;
  const goal = () => {
    const box = calc();
    const h1 = parseFloat(box?.dataset.h1 ?? "");
    const h2 = parseFloat(box?.dataset.h2 ?? "");
    if (!Number.isFinite(h1) || !Number.isFinite(h2)) return { hA: 0, hB: 0, on: 0.3 };
    const scale = MAXH / Math.max(REF, h1, h2);
    return { hA: h1 * scale, hB: h2 * scale, on: 1 };
  };
  return {
    kind: "stacks",
    scene,
    camera: ctx.cam,
    mats,
    pose() {
      const p = ctx.page;
      const g = goal();
      const want = {
        ...g,
        // On phones the camera keeps more distance, so the towers stay small behind the one column of text.
        d: track(p, [[0, 34], [0.3, 22], [0.6, 20], [1, 30]]) * (phone ? 1.6 : 1),
        theta: track(p, [[0, 0], [0.35, 0], [0.65, -side * 0.28], [0.9, 0]]),
        rise: track(p, [[0, 0], [0.3, 1], [0.65, 1], [0.95, 0]]),
      };
      const k = ready ? 0.08 : 1;
      ready = true;
      st.hA += (want.hA - st.hA) * k;
      st.hB += (want.hB - st.hB) * k;
      st.on += (want.on - st.on) * k;
      st.d += (want.d - st.d) * k;
      st.theta += (want.theta - st.theta) * k;
      st.rise += (want.rise - st.rise) * k;
      // The camera rises to just above the top of the shorter tower, where the gap begins.
      const gapAt = Math.max(EYE, Math.min(st.hA, st.hB) + 0.6);
      st.alt = EYE + (gapAt - EYE) * st.rise;
      st.x = cx + Math.sin(st.theta) * st.d;
      st.z = cz + Math.cos(st.theta) * st.d;
      // Look at the towers, then turn a little, so they stay on the inline-end side of the screen.
      const yawTo = Math.atan2(-(cx - st.x), -(cz - st.z));
      const turn = ctx.cam.aspect > 1 ? Math.atan(0.45 * Math.tan((ctx.cam.fov * Math.PI) / 360) * ctx.cam.aspect) : 0;
      const tall = Math.max(st.hA, st.hB);
      const lookH = tall * 0.45 + ((st.hA + st.hB) / 2 - tall * 0.45) * st.rise;
      const pitch = Math.min(0.4, Math.max(-0.25, Math.atan2(lookH - st.alt, st.d)));
      return { alt: st.alt, pitch, roll: 0, yaw: yawTo + side * turn, runway: 0, ground: { x: st.x, z: st.z }, view: st.alt, wind: 0, sway: 0.3, exact: true };
    },
    update(_p, t, alpha) {
      world.position.set(-st.x, 0, -st.z);
      sky.update(t, alpha);
      const a = alpha * st.on * dim(ctx) * (phone ? 0.45 : 1);
      today.update(st.hA, t, a);
      homayar.update(st.hB, t, a);
      // Gap: dotted edges of the "with Homayar" tower from its top up to the top of "today".
      const lo = st.hB;
      const hi = st.hA;
      const gapOn = hi - lo > 0.05 ? 1 : 0;
      let n = 0;
      if (gapOn) {
        const corners = [[-HALF, -HALF], [HALF, -HALF], [HALF, HALF], [-HALF, HALF]];
        for (const [x, z] of corners) {
          for (let y = lo + 0.3; y < hi && n < GN - 32; y += 0.3) gp.set([x, y, z], 3 * n++);
        }
        for (let c = 0; c < 4; c++) {
          const [x0, z0] = corners[c];
          const [x1, z1] = corners[(c + 1) % 4];
          for (let s = 0; s < 8; s++) gp.set([x0 + ((x1 - x0) * s) / 8, hi, z0 + ((z1 - z0) * s) / 8], 3 * n++);
        }
      }
      gGeo.setDrawRange(0, n);
      gGeo.attributes.position.needsUpdate = true;
      gapMat.uniforms.uOpacity.value = (0.75 + 0.2 * Math.sin(t * 2.4)) * gapOn * a;
      // The bracket stands on the outer side, away from "today", at the front corner of the tower.
      const zf = cz + HALF;
      const xe = xB + side * HALF;
      const xo = xB + side * (HALF + 0.3);
      const ys = [floorY + lo, floorY + hi];
      lp.set([xo, ys[0], zf, xo, ys[1], zf, xe, ys[0], zf, xo, ys[0], zf, xe, ys[1], zf, xo, ys[1], zf]);
      lGeo.attributes.position.needsUpdate = true;
      levelMat.uniforms.uOpacity.value = 0.85 * gapOn * a;
    },
    dispose() {
      scene.traverse((o) => (o as Mesh).geometry?.dispose());
      mats.forEach((m) => m.dispose());
    },
  };
}

// HTML hung in the 3D world. The section holds a fixed layer ([data-html-layer]) with a camera element
// ([data-html-camera]); each piece of HTML inside it is placed at an Object3D with CSS 3D transforms that use
// the scene camera, the method of three.js CSS3DRenderer. The text stays real, sharp HTML.
function htmlStage(ctx: Ctx, section: HTMLElement | null) {
  const layer = section?.querySelector<HTMLElement>("[data-html-layer]") ?? null;
  const camEl = section?.querySelector<HTMLElement>("[data-html-camera]") ?? null;
  const placed = new Set<HTMLElement>();
  const v = new Vector3();
  const normal = new Vector3();
  const toCam = new Vector3();
  section?.setAttribute("data-html-3d", "");
  return {
    // World units per CSS pixel, so that HTML seen from distance d keeps its CSS size on screen.
    px(d: number) {
      return (2 * d * Math.tan((ctx.cam.fov * Math.PI) / 360)) / window.innerHeight;
    },
    // Call once per frame, after the camera is set. The layer fades out once the section scrolls away,
    // and fades in while the content above it scrolls away, so it never covers other sections.
    begin(alpha: number) {
      if (!layer || !camEl || !section) return;
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      ctx.cam.updateMatrixWorld();
      const persp = ctx.cam.projectionMatrix.elements[5] * (vh / 2);
      const r = section.getBoundingClientRect();
      const fade = Math.min(1, Math.max(0, (r.bottom - vh * 0.3) / (vh * 0.5))) * Math.min(1, Math.max(0, 1 - r.top / (vh * 0.6)));
      layer.style.perspective = `${persp}px`;
      layer.style.opacity = String(fade * alpha);
      camEl.style.transform = `translateZ(${persp}px)${cssCamera(ctx.cam.matrixWorldInverse.elements)}translate(${vw / 2}px,${vh / 2}px)`;
    },
    // Hang one element at an object. Its world matrix must be up to date.
    place(el: HTMLElement, obj: Object3D, far = 80, dim = 1) {
      placed.add(el);
      // Hide HTML behind the camera or very far away. CSS 3D cannot clip it like WebGL does.
      v.setFromMatrixPosition(obj.matrixWorld);
      // Hide HTML seen from behind (CSS backface-visibility does not work through the faded layer) and fade it
      // when seen almost edge-on, where the text cannot be read anyway.
      toCam.setFromMatrixPosition(ctx.cam.matrixWorld).sub(v).normalize();
      normal.setFromMatrixColumn(obj.matrixWorld, 2).normalize();
      const facing = normal.dot(toCam);
      v.applyMatrix4(ctx.cam.matrixWorldInverse);
      const dist = -v.z;
      if (dist < 0.6 || dist > far || facing <= 0.05) {
        el.style.visibility = "hidden";
        return;
      }
      el.style.visibility = "";
      el.style.opacity = String((1 - smooth(far * 0.55, far, dist)) * smooth(0.05, 0.3, facing) * dim);
      el.style.transform = `translate(-50%,-50%)${cssObject(obj.matrixWorld.elements)}`;
    },
    dispose() {
      section?.removeAttribute("data-html-3d");
      placed.forEach((el) => el.removeAttribute("style"));
      layer?.removeAttribute("style");
      camEl?.removeAttribute("style");
    },
  };
}

// CSS 3D matrices for HTML that moves with the scene camera (the same math as three.js CSS3DRenderer).
// CSS has y pointing down, so the camera flips its y column and objects flip their y axis.
function cssNum(v: number) {
  return Math.abs(v) < 1e-10 ? 0 : v;
}
function cssCamera(e: ArrayLike<number>) {
  const m = [e[0], -e[1], e[2], e[3], e[4], -e[5], e[6], e[7], e[8], -e[9], e[10], e[11], e[12], -e[13], e[14], e[15]];
  return `matrix3d(${m.map(cssNum).join(",")})`;
}
function cssObject(e: ArrayLike<number>) {
  const m = [e[0], e[1], e[2], e[3], -e[4], -e[5], -e[6], -e[7], e[8], e[9], e[10], e[11], e[12], e[13], e[14], e[15]];
  return `matrix3d(${m.map(cssNum).join(",")})`;
}

function makeDescent(ctx: Ctx): Unit {
  return {
    kind: "field",
    scene: new Scene(),
    camera: ctx.cam,
    mats: [],
    pose() {
      const d = ctx.descent;
      const k = Math.min(1, Math.max(0, (d - 0.9) / 0.06));
      const bump = k > 0 && k < 1 ? Math.sin(k * Math.PI) * (1 - k) : 0;
      return {
        alt:
          track(d, [
            [0, HIGH],
            [0.7, 3.2],
            [0.9, GROUND + 0.25],
            [0.96, GROUND],
          ]) -
          0.12 * bump,
        pitch: track(d, [
          [0, -0.03],
          [0.12, -0.1],
          [0.75, -0.1],
          [0.9, 0.03],
          [0.97, -0.03],
        ]),
        roll: 0.015 * Math.sin(d * 20) * (1 - smooth(0.7, 0.9, d)),
        runway: smooth(0.55, 0.8, d),
      };
    },
    update() {},
    dispose() {},
  };
}

const PARTICLE_VERT = `
attribute vec3 aSphere; attribute vec2 aText; attribute vec3 aSky; attribute float aDelay; attribute float aSize;
uniform float uProgress; uniform float uBurst; uniform float uTime; uniform float uSphereScale; uniform float uTextScale; uniform float uDpr; uniform float uSize;
uniform vec2 uPointer;
varying float vDepth; varying float vMix; varying float vNear; varying float vTwinkle;
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
  vDepth = clamp((pos.z + 3.5) / 7.0, 0.0, 1.0);
  // The hit: each dot is kicked outward, then flies up to its own place in the sky and floats there.
  float b = smoothstep(aDelay * 0.3, aDelay * 0.3 + 0.7, uBurst);
  float e = 1.0 - pow(1.0 - b, 3.0);
  float arc = sin(b * 3.14159);
  vec3 kick = normalize(vec3(pos.xy, 0.0) + vec3(0.0, 0.001, 0.0)) * arc * 2.2 + vec3(0.0, arc * 1.2, 0.0);
  vec3 drift = vec3(sin(uTime * 0.21 + aDelay * 40.0), sin(uTime * 0.27 + aDelay * 23.0), 0.0) * 0.45 * e;
  pos = mix(pos, aSky, e) + kick + drift;
  vDepth = mix(vDepth, 1.0, e);
  vTwinkle = mix(1.0, 0.55 + 0.45 * sin(uTime * 1.7 + aDelay * 60.0), e);
  vec4 mv = modelViewMatrix * vec4(pos, 1.0);
  vNear = smoothstep(0.5, 3.5, -mv.z);
  gl_PointSize = uSize * aSize * uDpr * (1.0 + 0.35 * t + 1.2 * e) * min(3.0, 9.6 / max(1.0, -mv.z));
  gl_Position = projectionMatrix * mv;
}`;
const PARTICLE_FRAG = `
precision mediump float;
uniform vec3 uColor; uniform vec3 uDeep; uniform vec3 uBright; uniform float uOpacity; uniform float uSoft;
varying float vDepth; varying float vMix; varying float vNear; varying float vTwinkle;
void main() {
  vec2 c = gl_PointCoord - 0.5;
  float d = length(c);
  float a = smoothstep(0.5, uSoft, d);
  vec3 col = mix(uDeep, uColor, vDepth);
  col = mix(col, uBright, vMix * 0.45);
  float al = a * uOpacity * (0.55 + 0.45 * vDepth) * vNear * vTwinkle;
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
  const camera = ctx.cam;
  const N = ctx.light ? 2600 : 6000;
  const rnd = mulberry(21);
  const sphere = new Float32Array(N * 3);
  const text = new Float32Array(N * 2);
  const delay = new Float32Array(N);
  const size = new Float32Array(N);
  const sky = new Float32Array(N * 3);
  const R = 3.3;
  // Distance from the camera where the word stops and bursts.
  const IMPACT = 4;
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
    // A spot in the sky: above the horizon, spread wide and deep. Stored relative to where the word bursts.
    const az = (rnd() - 0.5) * 2.6;
    const el = 0.03 + Math.pow(rnd(), 0.8) * 0.5;
    const dist = 18 + rnd() * 50;
    sky[i * 3] = Math.sin(az) * Math.cos(el) * dist;
    sky[i * 3 + 1] = Math.sin(el) * dist;
    sky[i * 3 + 2] = -Math.cos(az) * Math.cos(el) * dist + IMPACT;
  }
  const g = new BufferGeometry();
  g.setAttribute("position", new BufferAttribute(sphere, 3));
  g.setAttribute("aSphere", new BufferAttribute(sphere, 3));
  const textAttr = new BufferAttribute(text, 2);
  g.setAttribute("aText", textAttr);
  g.setAttribute("aSky", new BufferAttribute(sky, 3));
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
        uBurst: { value: 0 },
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
  const cloud = new Group();
  // The shader moves the dots far from their sphere, so three.js must not cull them by the sphere's bounds.
  const glowPts = new Points(g, glow);
  const corePts = new Points(g, core);
  glowPts.frustumCulled = false;
  corePts.frustumCulled = false;
  cloud.add(glowPts, corePts);
  scene.add(cloud);

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

  // Distance from the camera where the sphere rests and the word forms.
  const HOLD = 8;
  // How far away the sphere starts.
  const START = 48;
  // Height that puts the sphere in the middle of the screen once the camera flies at SKY and reaches HOLD.
  const sphereY = -FLOOR_H + SKY - Math.tan(-CRUISE.pitch) * HOLD;
  // Scroll points of the hero story.
  const NOSE_UP = 0.12;
  const LIFT = 0.22;
  const ARRIVE = 0.5;
  const FORMED = 0.68;
  const RUSH = 0.74;
  const HIT = 0.86;
  return {
    kind: "particles",
    scene,
    camera,
    mats,
    // The page opens on the runway with the sphere high in the sky. Scrolling rolls along the runway, lifts the nose,
    // takes off and flies to the sphere. There the dots form the word, the word flies at the camera and bursts into stars.
    pose: (p) => ({
      alt: track(p, [
        [0, GROUND],
        [LIFT, GROUND],
        [0.44, SKY],
      ]),
      pitch: track(p, [
        [0, -0.03],
        [NOSE_UP, -0.03],
        [LIFT, 0.12],
        [0.36, 0.08],
        [ARRIVE - 0.02, CRUISE.pitch],
      ]),
      roll: 0,
      runway: 1,
    }),
    update(p, t, alpha) {
      const sp = ctx.smoothPointer;
      const visibleH = 2 * Math.tan((camera.fov * Math.PI) / 360) * HOLD;
      const visibleW = visibleH * camera.aspect;
      const textScale = Math.min(1, (visibleW * 0.92) / 14);
      const sphereScale = Math.min(1, (Math.min(visibleW, visibleH) * 0.8) / (2 * R));
      const progress = smooth(ARRIVE, FORMED, p);
      const pass = Math.min(1, Math.max(0, (p - RUSH) / (HIT - RUSH)));
      const rush = pass * pass;
      const burst = Math.min(1, Math.max(0, (p - HIT + 0.02) / (1 - HIT + 0.02)));
      const dist = track(p, [
        [0, START],
        [ARRIVE, HOLD],
      ]) - (HOLD - IMPACT) * rush;
      // Before the hit the sphere stays at its sky height. From the hit on, the cloud keeps the camera height, so the stars stay in the sky.
      const camY = -FLOOR_H + ctx.alt;
      cloud.position.set(0, sphereY + (camY - sphereY) * rush, -dist);
      for (const m of mats) {
        m.uniforms.uProgress.value = progress;
        m.uniforms.uBurst.value = burst;
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

  const ctx: Ctx = { theme: opts.theme, light: opts.light, rtl: opts.rtl, dpr, pointer: sceneBus.pointer, smoothPointer: { x: 0, y: 0 }, cam: floorCamera(), travel: 0, alt: CRUISE.alt, pitch: CRUISE.pitch, descent: 0, page: 0 };
  // One floor for all FLOOR_KINDS scenes. It moves with the page scroll, so it keeps going across section changes.
  const floor = buildFloor(ctx);
  const floorScene = new Scene();
  const runway = buildRunway(ctx);
  const streaks = buildStreaks(ctx);
  floorScene.add(floor.group, runway.group, streaks.group);
  let floorAlpha = 0;
  let floorTravel: number | null = null;
  let floorSpeed = 0;
  let pose: Pose | null = null;
  const rig = { x: 0, y: 0, rx: 0, ry: 0 };
  const units = new Map<SceneKind, Unit>();
  const unitFor = (kind: SceneKind, el: HTMLElement): Unit => {
    let u = units.get(kind);
    if (!u) {
      u = kind === "brain" ? makeBrain(ctx) : kind === "field" ? makeDescent(ctx) : kind === "ride" ? makeRide(ctx, el) : kind === "map" ? makeMap(ctx, el) : kind === "vault" ? makeVault(ctx, el) : kind === "gallery" ? makeGallery(ctx, el) : kind === "orbit" ? makeOrbit(ctx, el) : kind === "lift" ? makeLift(ctx, el) : kind === "beacon" ? makeBeacon(ctx) : kind === "dome" ? makeDome(ctx, el) : kind === "stacks" ? makeStacks(ctx, el) : kind === "particles" ? makeParticles(ctx, el) : kind === "warp" ? makeClimb(ctx) : makeTunnel(ctx);
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
    ctx.cam.aspect = w / h;
    ctx.cam.updateProjectionMatrix();
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
    const sp = ctx.smoothPointer;
    rig.x += (sp.x * 1.6 - rig.x) * 0.08;
    rig.y += (sp.y * 1.0 - rig.y) * 0.08;
    rig.ry += (-sp.x * 0.18 - rig.ry) * 0.08;
    rig.rx += (sp.y * 0.12 - rig.rx) * 0.08;
    const dt = Math.min(0.05, clock.getDelta() || 0.016);

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
    const progressOf = (sec: Live) => {
      const p = sec.pinned ? sceneBus.get(sec.el) : (sec.trigger?.progress ?? 0);
      if (sec.kind !== "brain" || p > 0) return p;
      // Before the brain section pins, its progress runs from -1 (top is BRAIN_APPROACH screens below) to 0 (top reaches the top).
      const top = sec.el.getBoundingClientRect().top;
      return -Math.min(1, Math.max(0, top / (BRAIN_APPROACH * height)));
    };
    if (previous) {
      previous.fade -= dt * 2.2;
      if (previous.fade <= 0 || (current && previous.unit === current.unit)) previous = null;
    }
    const floorOn = current !== null && FLOOR_KINDS.includes(current.unit.kind);
    const firstField = sections.find((s) => s.kind === "field");
    if (firstField) {
      // A pinned section sits inside a GSAP pin-spacer; measure the spacer so the start does not move while pinned.
      const box = firstField.el.parentElement?.classList.contains("pin-spacer") ? firstField.el.parentElement : firstField.el;
      const start = box.getBoundingClientRect().top + window.scrollY - height;
      const end = Math.max(start + 1, document.documentElement.scrollHeight - height);
      ctx.descent = Math.min(1, Math.max(0, (window.scrollY - start) / (end - start)));
    }
    ctx.page = Math.min(1, Math.max(0, window.scrollY / Math.max(1, document.documentElement.scrollHeight - height)));
    // After the burst, the hero dots stay in the sky for the rest of the page. They are drawn as a sky layer, not faded out.
    const sky = units.get("particles");
    const skySection = sections.find((s) => s.kind === "particles");
    const skyOn = !!sky && !!skySection && current?.unit !== sky && progressOf(skySection) >= 0.8;
    if (skyOn && previous?.unit === sky) previous = null;
    // The brain shows far away before its section arrives and grows while the page scrolls toward it.
    const brainSection = sections.find((s) => s.kind === "brain");
    const pBrain = brainSection ? progressOf(brainSection) : 1;
    const nearOn = !!brainSection && pBrain > -1 && pBrain <= 0 && current?.unit.kind !== "brain";
    let near: Unit | null = null;
    if (nearOn && brainSection) {
      const fresh = !units.has("brain");
      near = unitFor("brain", brainSection.el);
      if (fresh) {
        near.camera.aspect = width / height;
        near.camera.updateProjectionMatrix();
      }
      if (previous?.unit === near) previous = null;
    }
    const pPrev = previous ? progressOf(previous.section) : 0;
    const pCur = current ? progressOf(current.section) : 0;

    floorAlpha += ((floorOn ? 1 : 0) - floorAlpha) * Math.min(1, dt * 4);
    const travelTarget = (window.scrollY / Math.max(1, height)) * FLOOR_SPEED;
    const lastTravel = floorTravel ?? travelTarget;
    floorTravel = lastTravel + (travelTarget - lastTravel) * 0.15;
    ctx.travel = floorTravel;
    const speedTarget = Math.min(1, Math.abs(floorTravel - lastTravel) / dt / 30);
    floorSpeed += (speedTarget - floorSpeed) * (speedTarget > floorSpeed ? 0.35 : 0.06);

    // Glide the shared camera toward the pose the active floor scene asks for.
    const want = floorOn && current?.unit.pose ? current.unit.pose(pCur) : null;
    if (want?.exact) pose = { ...want };
    else if (want) {
      if (!pose) pose = { ...want };
      pose.alt += (want.alt - pose.alt) * 0.12;
      pose.pitch += (want.pitch - pose.pitch) * 0.12;
      pose.roll += (want.roll - pose.roll) * 0.12;
      pose.runway += (want.runway - pose.runway) * 0.12;
      pose.yaw = (pose.yaw ?? 0) + ((want.yaw ?? 0) - (pose.yaw ?? 0)) * 0.12;
      pose.view = (pose.view ?? 0) + ((want.view ?? 0) - (pose.view ?? 0)) * 0.12;
      pose.ground = want.ground;
    }
    const at = pose ?? CRUISE;
    ctx.alt = at.alt;
    ctx.pitch = at.pitch;
    // Less pointer sway near the ground, so the camera never dips below the floor.
    const low = Math.max(0.2, Math.min(1, at.alt / CRUISE.alt));
    const sw = at.sway ?? 1;
    ctx.cam.position.set(rig.x * low * sw, -FLOOR_H + at.alt + rig.y * low * sw, 0);
    ctx.cam.rotation.set(at.pitch + rig.rx * sw, (at.yaw ?? 0) + rig.ry * sw, at.roll);

    if (previous) previous.unit.update(pPrev, t, previous.fade);
    // The brain does not dip when it takes over, because the approach already shows it at full strength.
    if (current) current.unit.update(pCur, t, previous && current.unit.kind !== "brain" ? 1 - previous.fade * 0.5 : 1);
    if (floorAlpha > 0.005) {
      const g = pose?.ground;
      floor.update(g ? -g.z : floorTravel, floorAlpha, floorSpeed, g ? g.x : 0, pose?.view ?? 0);
      runway.update(floorTravel, floorAlpha * (pose?.runway ?? 0));
      streaks.update(floorTravel, floorSpeed, floorAlpha * (pose?.wind ?? 1));
      renderer.render(floorScene, ctx.cam);
    }
    if (skyOn && sky && skySection) {
      sky.update(progressOf(skySection), t, 1);
      renderer.render(sky.scene, sky.camera);
    }
    if (near) {
      near.update(pBrain, t, 1);
      renderer.render(near.scene, near.camera);
    }
    if (previous) renderer.render(previous.unit.scene, previous.unit.camera);
    if (current) renderer.render(current.unit.scene, current.unit.camera);
    if (current || previous || floorAlpha > 0.005 || skyOn) raf = requestAnimationFrame(frame);
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
      [...floor.mats, ...runway.mats, ...streaks.mats].forEach((m) => {
        (m.uniforms.uColor.value as Color).set(pal[m.userData.role]);
        applyBlend(m, theme);
      });
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
      floorScene.traverse((o) => (o as Mesh).geometry?.dispose());
      floor.mats.forEach((m) => m.dispose());
      runway.mats.forEach((m) => m.dispose());
      streaks.mats.forEach((m) => m.dispose());
      renderer.dispose();
    },
  };
}
