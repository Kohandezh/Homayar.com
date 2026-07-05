"use client";

import { useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Bloom, EffectComposer } from "@react-three/postprocessing";
import * as THREE from "three";

const ACCENT = new THREE.Color("#22d3ee");
const VIOLET = new THREE.Color("#8b5cf6");
const TEAL = new THREE.Color("#2dd4bf");
const WHITE = new THREE.Color("#eafaff");
const HOT = new THREE.Color("#ccfbff"); // bright peak tint for loud bars

/** Soft radial sprite so every particle is a round, glowing dot — not a square. */
function useGlowTexture() {
  return useMemo(() => {
    const size = 128;
    const c = document.createElement("canvas");
    c.width = c.height = size;
    const ctx = c.getContext("2d")!;
    const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    g.addColorStop(0, "rgba(255,255,255,1)");
    g.addColorStop(0.25, "rgba(255,255,255,0.85)");
    g.addColorStop(0.55, "rgba(255,255,255,0.25)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, size, size);
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    return tex;
  }, []);
}

const fract = (x: number) => x - Math.floor(x);
/** Deterministic per-index pseudo-random (no Math.random — keeps SSR/replay safe). */
const rand = (i: number) => fract(Math.sin(i * 127.1) * 43758.5453);

/**
 * The hero: a glowing orb that "listens" — it breathes, is ringed by a circular
 * audio equalizer whose bars rise and fall like live speech, and emits soft sonar
 * ripples. Reads instantly as a voice AI agent. Cursor tilts the whole rig for depth.
 */
function ListeningOrb({ density }: { density: "low" | "high" }) {
  const low = density === "low";
  const group = useRef<THREE.Group>(null);
  const orbGroup = useRef<THREE.Group>(null);
  const barRef = useRef<THREE.InstancedMesh>(null);
  const r0 = useRef<THREE.Mesh>(null);
  const r1 = useRef<THREE.Mesh>(null);
  const r2 = useRef<THREE.Mesh>(null);

  const glow = useGlowTexture();
  const pointer = useRef({ x: 0, y: 0 });
  const target = useRef({ x: 0, y: 0 });

  const BARS = low ? 44 : 64;
  const ORB = low ? 800 : 1400;
  const CORE = low ? 180 : 320;
  const BAR_LEN = low ? 1.0 : 1.35;
  const BAR_W = low ? 0.06 : 0.05;

  // Cursor drives the tilt via a window listener (canvas is pointer-events:none,
  // so r3f's state.pointer never fires — this keeps the orb reactive everywhere).
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      target.current.x = (e.clientX / window.innerWidth) * 2 - 1;
      target.current.y = -((e.clientY / window.innerHeight) * 2 - 1);
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => window.removeEventListener("pointermove", onMove);
  }, []);

  // Orb shell — a Fibonacci sphere of accent→violet particles.
  const orb = useMemo(() => {
    const positions = new Float32Array(ORB * 3);
    const colors = new Float32Array(ORB * 3);
    const c = new THREE.Color();
    const golden = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < ORB; i++) {
      const t = i / ORB;
      const y = 1 - t * 2;
      const r = Math.sqrt(1 - y * y);
      const th = golden * i;
      const rad = 1.4 + rand(i) * 0.16;
      positions[i * 3] = Math.cos(th) * r * rad;
      positions[i * 3 + 1] = y * rad;
      positions[i * 3 + 2] = Math.sin(th) * r * rad;
      c.copy(ACCENT).lerp(VIOLET, Math.abs(y));
      if ((i & 29) === 0) c.copy(TEAL);
      if ((i & 53) === 0) c.copy(WHITE);
      colors[i * 3] = c.r;
      colors[i * 3 + 1] = c.g;
      colors[i * 3 + 2] = c.b;
    }
    return { positions, colors };
  }, [ORB]);

  // Bright near-white inner core for the hard bloom "shine".
  const core = useMemo(() => {
    const positions = new Float32Array(CORE * 3);
    const golden = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < CORE; i++) {
      const t = i / CORE;
      const y = 1 - t * 2;
      const r = Math.sqrt(1 - y * y);
      const th = golden * i;
      const rad = 0.45 + rand(i + 7) * 0.5;
      positions[i * 3] = Math.cos(th) * r * rad;
      positions[i * 3 + 1] = y * rad;
      positions[i * 3 + 2] = Math.sin(th) * r * rad;
    }
    return positions;
  }, [CORE]);

  // Equalizer bars: instanced boxes, geometry + material owned here (TS-clean,
  // and r3f disposes them with the mesh on unmount).
  const barGeo = useMemo(() => new THREE.BoxGeometry(1, 1, 1), []);
  const barMat = useMemo(
    () =>
      new THREE.MeshBasicMaterial({
        transparent: true,
        opacity: 0.95,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        toneMapped: false,
      }),
    [],
  );
  const baseColors = useMemo(
    () =>
      Array.from({ length: BARS }, (_, i) =>
        new THREE.Color().copy(ACCENT).lerp(VIOLET, i / BARS),
      ),
    [BARS],
  );
  const dummy = useMemo(() => new THREE.Object3D(), []);
  const tmpColor = useMemo(() => new THREE.Color(), []);

  useFrame((state, delta) => {
    const t = state.clock.elapsedTime;

    // Whole rig tilts toward the cursor (eased) + a gentle idle float.
    const g = group.current;
    if (g) {
      pointer.current.x += (target.current.x - pointer.current.x) * 0.06;
      pointer.current.y += (target.current.y - pointer.current.y) * 0.06;
      g.rotation.x = pointer.current.y * 0.28;
      g.rotation.y = pointer.current.x * 0.34;
      g.position.y = Math.sin(t * 0.5) * 0.04;
    }

    // Speech-like global envelope: bursts of "loudness" that swell and fade.
    const env = 0.45 + 0.55 * Math.abs(Math.sin(t * 0.8) * Math.sin(t * 0.37 + 1.0));

    // Orb breathes with the envelope.
    if (orbGroup.current) {
      const s = 1 + Math.sin(t * 1.5) * 0.03 + env * 0.05;
      orbGroup.current.scale.setScalar(s);
      orbGroup.current.rotation.y += delta * 0.08;
    }

    // Equalizer: each bar's height = layered sines (organic, speech-like).
    const bar = barRef.current;
    if (bar) {
      const baseR = 1.9;
      for (let i = 0; i < BARS; i++) {
        const a = (i / BARS) * Math.PI * 2;
        const rnd = rand(i);
        let amp =
          0.5 * Math.sin(t * 1.3 + i * 0.5) +
          0.3 * Math.sin(t * 2.1 + i * 0.27 + rnd * 6.28) +
          0.2 * Math.sin(t * 3.7 + i * 0.9);
        amp = Math.max(0, amp) * (0.55 + env * 0.7);
        const len = 0.14 + amp * BAR_LEN;
        const midR = baseR + len / 2;
        dummy.position.set(Math.cos(a) * midR, Math.sin(a) * midR, 0);
        dummy.rotation.set(0, 0, a - Math.PI / 2); // box height (Y) points radially
        dummy.scale.set(BAR_W, len, BAR_W);
        dummy.updateMatrix();
        bar.setMatrixAt(i, dummy.matrix);

        const peak = Math.min(1, amp * 1.1);
        tmpColor.copy(baseColors[i]).lerp(HOT, peak * 0.7);
        bar.setColorAt(i, tmpColor);
      }
      bar.instanceMatrix.needsUpdate = true;
      if (bar.instanceColor) bar.instanceColor.needsUpdate = true;
    }

    // Sonar ripples: three rings expand outward from the orb and fade.
    const rings = [r0.current, r1.current, r2.current];
    for (let i = 0; i < rings.length; i++) {
      const m = rings[i];
      if (!m) continue;
      const phase = fract(t / 3.4 + i / rings.length); // 0→1 loop, staggered
      const sc = 1 + phase * 0.75;
      m.scale.set(sc, sc, sc);
      (m.material as THREE.MeshBasicMaterial).opacity =
        (1 - phase) * 0.22 * (0.45 + env * 0.7);
    }
  });

  return (
    <group ref={group}>
      <group ref={orbGroup}>
        <points>
          <bufferGeometry>
            <bufferAttribute attach="attributes-position" args={[orb.positions, 3]} />
            <bufferAttribute attach="attributes-color" args={[orb.colors, 3]} />
          </bufferGeometry>
          <pointsMaterial
            map={glow}
            vertexColors
            size={0.05}
            sizeAttenuation
            transparent
            opacity={0.9}
            depthWrite={false}
            blending={THREE.AdditiveBlending}
          />
        </points>
        <points>
          <bufferGeometry>
            <bufferAttribute attach="attributes-position" args={[core, 3]} />
          </bufferGeometry>
          <pointsMaterial
            map={glow}
            color={WHITE}
            size={0.13}
            sizeAttenuation
            transparent
            opacity={0.95}
            depthWrite={false}
            blending={THREE.AdditiveBlending}
          />
        </points>
      </group>

      <instancedMesh ref={barRef} args={[barGeo, barMat, BARS]} />

      <Ripple meshRef={r0} />
      <Ripple meshRef={r1} />
      <Ripple meshRef={r2} />
    </group>
  );
}

/** One sonar ring. Animated (scale + opacity) from the parent useFrame loop. */
function Ripple({ meshRef }: { meshRef: React.RefObject<THREE.Mesh | null> }) {
  return (
    <mesh ref={meshRef}>
      <ringGeometry args={[1.9, 1.94, 96]} />
      <meshBasicMaterial
        color={ACCENT}
        transparent
        opacity={0.18}
        side={THREE.DoubleSide}
        blending={THREE.AdditiveBlending}
        depthWrite={false}
        toneMapped={false}
      />
    </mesh>
  );
}

export default function VoiceField({
  density = "high",
  bloom = true,
  visible = true,
}: {
  density?: "low" | "high";
  bloom?: boolean;
  /** When false the render loop stops entirely (hero offscreen / tab hidden). */
  visible?: boolean;
}) {
  const low = density === "low";
  return (
    <Canvas
      // Stop rendering completely when the hero isn't on screen — this is the
      // single biggest win: no GPU/CPU spent while the user reads the rest of
      // the page, which also keeps the main thread free so the UI stays snappy.
      frameloop={visible ? "always" : "never"}
      camera={{ position: [0, 0, 9], fov: 45 }}
      // Hard-cap pixel density. A decorative effect does not need retina fill
      // rate; 2.5x DPR over a 700px canvas with bloom is what made it crawl.
      dpr={low ? [1, 1.25] : [1, 1.5]}
      gl={{
        antialias: !low,
        alpha: true,
        // "default" lets weak/old GPUs use their integrated path instead of
        // refusing a high-performance context; the rest keeps fallbacks open.
        powerPreference: low ? "default" : "high-performance",
        toneMapping: THREE.ACESFilmicToneMapping,
        failIfMajorPerformanceCaveat: false,
      }}
      style={{ pointerEvents: "none" }}
    >
      <ListeningOrb density={density} />
      {bloom && (
        <EffectComposer multisampling={0}>
          <Bloom
            intensity={1.4}
            luminanceThreshold={0.04}
            luminanceSmoothing={0.9}
            mipmapBlur
            radius={0.85}
          />
        </EffectComposer>
      )}
    </Canvas>
  );
}
