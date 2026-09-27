"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

const VoiceField = dynamic(() => import("./VoiceField"), { ssr: false });

const MOTION_OK = "(prefers-reduced-motion: no-preference)";

export function HeroStage({ children, label, rtl, className = "" }: { children: ReactNode; label: string; rtl: boolean; className?: string }) {
  const [scene, setScene] = useState<"pending" | "webgl" | "css">("pending");
  const [reduced, setReduced] = useState(false);
  const [light, setLight] = useState(false);
  const progress = useRef(0);
  const sectionRef = useRef<HTMLElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const onFail = useCallback(() => setScene("css"), []);

  useEffect(() => {
    let id = 0;
    const boot = () => {
      setReduced(!window.matchMedia(MOTION_OK).matches);
      setLight(window.innerWidth < 768);
      const probe = document.createElement("canvas");
      const ok = !!(probe.getContext("webgl2") || probe.getContext("webgl"));
      setScene(ok ? "webgl" : "css");
    };
    id = requestAnimationFrame(() => {
      id = requestAnimationFrame(boot);
    });
    return () => cancelAnimationFrame(id);
  }, []);

  useEffect(() => {
    if (reduced || scene === "pending") return;
    const section = sectionRef.current;
    const content = contentRef.current;
    if (!section || !content) return;
    let cleanup: (() => void) | undefined;
    let cancelled = false;
    import("@/lib/gsap").then(({ gsap, ScrollTrigger }) => {
      if (cancelled) return;
      const st = ScrollTrigger.create({
        trigger: section,
        start: "top top",
        end: "bottom top",
        scrub: true,
        onUpdate: (self) => {
          progress.current = self.progress;
        },
      });
      const tween = gsap.to(content, {
        yPercent: -12,
        opacity: 0.12,
        ease: "none",
        scrollTrigger: { trigger: section, start: "35% top", end: "bottom top", scrub: true },
      });
      cleanup = () => {
        st.kill();
        tween.scrollTrigger?.kill();
        tween.kill();
        progress.current = 0;
      };
    });
    return () => {
      cancelled = true;
      cleanup?.();
    };
  }, [reduced, scene]);

  return (
    <section ref={sectionRef} className={`relative isolate overflow-hidden ${className}`} data-testid="hero-stage">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10">
        {scene === "webgl" ? (
          <VoiceField progress={progress} reduced={reduced} light={light} rtl={rtl} label={label} onFail={onFail} />
        ) : (
          <div className={`absolute inset-0 ${scene === "css" ? "aurora" : ""}`} />
        )}
        <div className="absolute inset-0 grid-lines" />
        <div className="absolute inset-x-0 bottom-0 h-40 bg-gradient-to-b from-transparent to-bg" />
      </div>
      <div ref={contentRef} className="relative">
        {children}
      </div>
    </section>
  );
}
