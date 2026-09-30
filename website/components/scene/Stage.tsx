"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import { sceneBus, type SceneKind } from "./bus";
import type { Engine } from "./engine";

const MOTION_OK = "(prefers-reduced-motion: no-preference)";

export function Stage({ rtl }: { rtl: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<Engine | null>(null);
  const pathname = usePathname();

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (!window.matchMedia(MOTION_OK).matches) return;
    let cancelled = false;
    let armed = false;

    const onPointer = (e: PointerEvent) => {
      sceneBus.pointer.x = (e.clientX / window.innerWidth) * 2 - 1;
      sceneBus.pointer.y = -((e.clientY / window.innerHeight) * 2 - 1);
    };
    window.addEventListener("pointermove", onPointer, { passive: true });

    const boot = async () => {
      if (armed || cancelled) return;
      armed = true;
      const probe = document.createElement("canvas");
      const ok = !!(probe.getContext("webgl2") || probe.getContext("webgl"));
      if (!ok) {
        document.documentElement.classList.add("no-webgl");
        return;
      }
      const { createEngine } = await import("./engine");
      if (cancelled) return;
      const theme = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
      const engine = createEngine(canvas, { theme, light: window.innerWidth < 768, rtl });
      if (!engine) {
        document.documentElement.classList.add("no-webgl");
        return;
      }
      engineRef.current = engine;
      canvas.dataset.ready = "true";
      collect(engine);
    };

    const triggers = ["scroll", "pointermove", "keydown", "touchstart"] as const;
    const arm = () => {
      triggers.forEach((t) => window.removeEventListener(t, arm));
      void boot();
    };
    triggers.forEach((t) => window.addEventListener(t, arm, { passive: true, once: true }));
    const timer = window.setTimeout(arm, document.readyState === "complete" ? 1500 : 2500);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      triggers.forEach((t) => window.removeEventListener(t, arm));
      window.removeEventListener("pointermove", onPointer);
      engineRef.current?.dispose();
      engineRef.current = null;
    };
  }, [rtl]);

  useEffect(() => {
    const engine = engineRef.current;
    if (engine) {
      const t = window.setTimeout(() => collect(engine), 60);
      return () => window.clearTimeout(t);
    }
  }, [pathname]);

  useEffect(() => {
    const onTheme = (e: Event) => {
      const theme = (e as CustomEvent<"dark" | "light">).detail;
      engineRef.current?.setTheme(theme);
    };
    window.addEventListener("themechange", onTheme);
    return () => window.removeEventListener("themechange", onTheme);
  }, []);

  return (
    <div className="stage" aria-hidden="true" data-testid="stage">
      <canvas ref={canvasRef} data-testid="scene-canvas" />
    </div>
  );
}

function collect(engine: Engine) {
  const list = Array.from(document.querySelectorAll<HTMLElement>("[data-scene]")).map((el) => ({
    el,
    kind: el.dataset.scene as SceneKind,
    pinned: el.hasAttribute("data-pin"),
  }));
  engine.setSections(list);
}
