"use client";

import { useEffect, useRef, type RefObject } from "react";

const VERT = `
attribute vec2 aIndex;
attribute float aSeed;
uniform float uTime;
uniform vec2 uPointer;
uniform float uResolve;
uniform float uAspect;
uniform float uDpr;
uniform float uScale;
uniform float uDir;
varying float vAlpha;
varying float vMix;
void main() {
  float x = mix(-1.08, 1.08, aIndex.x);
  float band = aIndex.y - 0.5;
  float env = 0.3 + 0.7 * abs(sin(x * 2.3 + uTime * 0.9) * sin(x * 5.1 - uTime * 1.7 + aSeed));
  float wave = sin(x * 11.0 + uTime * 2.4 + aSeed * 2.0) * 0.12 * env + sin(x * 27.0 - uTime * 3.7) * 0.04 * env;
  float chaosY = band * 1.1 * env + wave + (aSeed - 0.5) * 0.06;
  float rows = 7.0;
  float row = floor(aIndex.y * rows);
  float rowY = ((row + 0.5) / rows - 0.5) * 1.15;
  float len = 0.5 + 0.45 * fract(sin(row * 12.9898) * 43758.5453);
  float resolvedX = uDir * mix(-0.92, -0.92 + 1.84 * len, aIndex.x);
  float resolvedY = rowY + sin(uTime * 1.2 + aIndex.x * 6.0 + row) * 0.006;
  float r = smoothstep(0.0, 1.0, uResolve);
  vec2 pos = mix(vec2(x, chaosY), vec2(resolvedX, resolvedY), r);
  vec2 d = pos - uPointer;
  d.x *= uAspect;
  float dist = length(d);
  float push = exp(-dist * dist * 9.0);
  pos += normalize(d + 1e-4) * push * 0.1 / vec2(uAspect, 1.0);
  gl_Position = vec4(pos, 0.0, 1.0);
  gl_PointSize = (1.6 + 2.4 * env * (1.0 - r) + 1.6 * r + 5.0 * push) * uScale * uDpr;
  vAlpha = mix(0.3 + 0.7 * env, 0.9, r) * (0.65 + 0.35 * aSeed);
  vMix = clamp(r + push * 0.7, 0.0, 1.0);
}`;

const FRAG = `
precision mediump float;
uniform vec3 uColorA;
uniform vec3 uColorB;
varying float vAlpha;
varying float vMix;
void main() {
  vec2 c = gl_PointCoord - 0.5;
  float d = length(c);
  float a = smoothstep(0.5, 0.12, d) * vAlpha;
  vec3 col = mix(uColorA, uColorB, vMix);
  gl_FragColor = vec4(col * a, a);
}`;

const AMBER: [number, number, number] = [0.96, 0.62, 0.04];
const TEAL: [number, number, number] = [0.18, 0.83, 0.75];

function compile(gl: WebGLRenderingContext, type: number, src: string) {
  const sh = gl.createShader(type);
  if (!sh) return null;
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) return null;
  return sh;
}

export default function VoiceField({
  progress,
  reduced,
  light,
  rtl,
  label,
  onFail,
}: {
  progress: RefObject<number>;
  reduced: boolean;
  light: boolean;
  rtl: boolean;
  label: string;
  onFail: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const gl = (canvas.getContext("webgl2", { alpha: true, antialias: false, powerPreference: "low-power", preserveDrawingBuffer: true }) ??
      canvas.getContext("webgl", { alpha: true, antialias: false, preserveDrawingBuffer: true })) as WebGLRenderingContext | null;
    if (!gl) {
      onFail();
      return;
    }

    const vs = compile(gl, gl.VERTEX_SHADER, VERT);
    const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
    const prog = gl.createProgram();
    if (!vs || !fs || !prog) {
      onFail();
      return;
    }
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      onFail();
      return;
    }
    gl.useProgram(prog);

    const cols = light ? 84 : 180;
    const rows = light ? 22 : 44;
    const count = cols * rows;
    const index = new Float32Array(count * 2);
    const seed = new Float32Array(count);
    let n = 0;
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        index[n * 2] = c / (cols - 1);
        index[n * 2 + 1] = r / (rows - 1);
        seed[n] = ((Math.sin(n * 12.9898 + r * 78.233) * 43758.5453) % 1 + 1) % 1;
        n++;
      }
    }

    const bind = (name: string, data: Float32Array, size: number) => {
      const buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      const loc = gl.getAttribLocation(prog, name);
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
      return buf;
    };
    const buffers = [bind("aIndex", index, 2), bind("aSeed", seed, 1)];

    const u = (name: string) => gl.getUniformLocation(prog, name);
    const uTime = u("uTime");
    const uPointer = u("uPointer");
    const uResolve = u("uResolve");
    const uAspect = u("uAspect");
    const uDpr = u("uDpr");
    const uScale = u("uScale");
    const uDir = u("uDir");
    gl.uniform3fv(u("uColorA"), AMBER);
    gl.uniform3fv(u("uColorB"), TEAL);
    gl.uniform1f(uScale, light ? 1.15 : 1);
    gl.uniform1f(uDir, rtl ? -1 : 1);

    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE);
    gl.clearColor(0, 0, 0, 0);

    const dpr = Math.min(window.devicePixelRatio || 1, light ? 1 : 1.5);
    let width = 1;
    let height = 1;
    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      width = Math.max(1, Math.round(rect.width * dpr));
      height = Math.max(1, Math.round(rect.height * dpr));
      canvas.width = width;
      canvas.height = height;
      gl.viewport(0, 0, width, height);
      gl.uniform1f(uAspect, width / height);
      gl.uniform1f(uDpr, dpr);
    };
    resize();

    const target = { x: 0, y: -3 };
    const pointer = { x: 0, y: -3 };
    const onPointer = (e: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      target.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      target.y = -(((e.clientY - rect.top) / rect.height) * 2 - 1);
    };
    const onLeave = () => {
      target.x = 0;
      target.y = -3;
    };

    const start = performance.now();
    let raf = 0;
    let visible = true;

    const draw = (now: number) => {
      pointer.x += (target.x - pointer.x) * 0.08;
      pointer.y += (target.y - pointer.y) * 0.08;
      gl.uniform1f(uTime, reduced ? 4.2 : (now - start) / 1000);
      gl.uniform2f(uPointer, pointer.x, pointer.y);
      gl.uniform1f(uResolve, progress.current ?? 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.POINTS, 0, count);
    };

    const loop = (now: number) => {
      raf = 0;
      if (!visible || document.hidden) return;
      draw(now);
      raf = requestAnimationFrame(loop);
    };
    const wake = () => {
      if (reduced) {
        draw(performance.now());
        return;
      }
      if (!raf && visible && !document.hidden) raf = requestAnimationFrame(loop);
    };

    const io = new IntersectionObserver(
      (entries) => {
        visible = entries.some((e) => e.isIntersecting);
        wake();
      },
      { threshold: 0.01 },
    );
    io.observe(canvas);
    const ro = new ResizeObserver(() => {
      resize();
      if (reduced) draw(performance.now());
    });
    ro.observe(canvas);
    document.addEventListener("visibilitychange", wake);
    if (!reduced) {
      window.addEventListener("pointermove", onPointer, { passive: true });
      window.addEventListener("pointerleave", onLeave);
    }
    wake();

    return () => {
      if (raf) cancelAnimationFrame(raf);
      io.disconnect();
      ro.disconnect();
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("pointermove", onPointer);
      window.removeEventListener("pointerleave", onLeave);
      buffers.forEach((b) => gl.deleteBuffer(b));
      gl.deleteProgram(prog);
      gl.deleteShader(vs);
      gl.deleteShader(fs);
      gl.getExtension("WEBGL_lose_context")?.loseContext();
    };
  }, [progress, reduced, light, rtl, onFail]);

  return <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" role="img" aria-label={label} data-testid="voice-field" />;
}
