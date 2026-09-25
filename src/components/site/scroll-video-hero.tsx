"use client";

import { useEffect, useRef } from "react";
import ReactDOM from "react-dom";
import { useMedia } from "@/lib/client-state";
import type { Dict } from "@/lib/content";

/**
 * Full-screen cinematic hero.
 *
 * Desktop (fine pointer, wide, motion OK) → scroll-scrubbed playback: the
 * section is taller than the viewport, the film pins (sticky) while you scroll,
 * and scroll progress drives the video's currentTime. The loop is paused the
 * moment the hero leaves the viewport or the tab is hidden.
 *
 * Touch devices (iPhone / Android / iPad) and reduced-motion → a clean static
 * poster hero. iOS Safari throttles rAF-driven video seeking and doesn't emit
 * scroll events during momentum scrolling, so scrubbing there janks badly —
 * the still hero looks identical at rest and stays perfectly smooth.
 *
 * RTL locales (fa/ar) get the rtl cut, LTR the ltr cut.
 */
export function ScrollVideoHero({ t, dir }: { t: Dict; dir: "rtl" | "ltr" }) {
  const sectionRef = useRef<HTMLElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const base = dir === "ltr" ? "rtl" : "ltr";

  // "static" on the server and during hydration (mobile-first, no hydration
  // mismatch); "scrub" only on a device that can do it well.
  const fine = useMedia("(pointer: fine)");
  const wide = useMedia("(min-width: 768px)");
  const reduced = useMedia("(prefers-reduced-motion: reduce)");
  const mode: "static" | "scrub" = fine && wide && !reduced ? "scrub" : "static";

  // Paint the LCP poster as early as the streamed <head>.
  ReactDOM.preload("/hero-poster.webp", { as: "image", fetchPriority: "high" });

  // ── Desktop scroll-scrub ──────────────────────────────────────────────────
  useEffect(() => {
    if (mode !== "scrub") return;
    const video = videoRef.current;
    const section = sectionRef.current;
    const overlay = overlayRef.current;
    if (!video || !section) return;

    video.pause();

    const lines = overlay
      ? Array.from(overlay.querySelectorAll<HTMLElement>(".hero-line"))
      : [];
    for (const line of lines) {
      line.style.transition = "opacity .6s ease, transform .6s ease";
      line.style.opacity = "0";
      line.style.transform = "translateY(24px)";
    }

    // Scroll fractions where each line appears, and where the block drifts away.
    const REVEAL_AT = [0, 0.1, 0.26];
    const OUT_START = 0.6;
    const OUT_END = 0.92;

    let raf = 0;
    let target = 0;
    let rendered = 0;
    let active = false;

    const measure = () => {
      const scrollable = section.offsetHeight - window.innerHeight;
      const p =
        scrollable > 0
          ? Math.min(Math.max(-section.getBoundingClientRect().top / scrollable, 0), 1)
          : 0;
      if (video.duration) target = p * video.duration;

      for (let i = 0; i < lines.length; i++) {
        const on = p >= (REVEAL_AT[i] ?? 0);
        lines[i].style.opacity = on ? "1" : "0";
        lines[i].style.transform = on ? "none" : "translateY(24px)";
        lines[i].style.pointerEvents = on ? "" : "none";
      }
      if (overlay) {
        const out = Math.min(Math.max((p - OUT_START) / (OUT_END - OUT_START), 0), 1);
        overlay.style.opacity = String(1 - out);
        overlay.style.transform = `translateY(${-out * 70}px) scale(${1 - out * 0.05})`;
        overlay.style.filter = out > 0 ? `blur(${out * 5}px)` : "";
        overlay.style.pointerEvents = out > 0.05 ? "none" : "";
      }
    };

    const tick = () => {
      if (video.duration) {
        rendered += (target - rendered) * 0.12;
        if (Math.abs(target - rendered) < 0.005) rendered = target;
        if (!Number.isNaN(rendered)) {
          try {
            video.currentTime = rendered;
          } catch {
            /* not seekable yet */
          }
        }
      }
      raf = active ? requestAnimationFrame(tick) : 0;
    };
    const start = () => {
      if (raf === 0) raf = requestAnimationFrame(tick);
    };

    // Run only while the hero is on screen and the tab is visible.
    const io = new IntersectionObserver(
      ([entry]) => {
        active = entry.isIntersecting && document.visibilityState === "visible";
        if (active) {
          measure();
          start();
        }
      },
      { threshold: 0 },
    );
    io.observe(section);

    const onVisibility = () => {
      const rect = section.getBoundingClientRect();
      active =
        document.visibilityState === "visible" &&
        rect.bottom > 0 &&
        rect.top < window.innerHeight;
      if (active) start();
    };

    video.addEventListener("loadedmetadata", measure);
    window.addEventListener("scroll", measure, { passive: true });
    window.addEventListener("resize", measure);
    document.addEventListener("visibilitychange", onVisibility);
    measure();

    return () => {
      cancelAnimationFrame(raf);
      io.disconnect();
      video.removeEventListener("loadedmetadata", measure);
      window.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
      document.removeEventListener("visibilitychange", onVisibility);
      for (const line of lines) line.style.pointerEvents = "";
      if (overlay) {
        overlay.style.opacity = "";
        overlay.style.transform = "";
        overlay.style.filter = "";
        overlay.style.pointerEvents = "";
      }
    };
  }, [mode, base]);

  // ── Static (mobile / reduced-motion): reveal overlay lines on mount ───────
  useEffect(() => {
    if (mode !== "static") return;
    const overlay = overlayRef.current;
    if (!overlay) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) return; // CSS leaves .hero-line visible under reduced motion
    const lines = Array.from(overlay.querySelectorAll<HTMLElement>(".hero-line"));
    lines.forEach((line, i) => {
      line.style.transition = "opacity .7s ease, transform .7s ease";
      line.style.transitionDelay = `${i * 0.12}s`;
      line.style.opacity = "0";
      line.style.transform = "translateY(24px)";
    });
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        for (const line of lines) {
          line.style.opacity = "1";
          line.style.transform = "none";
        }
      }),
    );
  }, [mode]);

  const scrub = mode === "scrub";

  return (
    <section
      id="home"
      ref={sectionRef}
      className={
        scrub
          ? "relative h-[300svh] w-full bg-bg"
          : "relative h-[100svh] w-full overflow-hidden bg-bg"
      }
    >
      <div
        className={
          scrub
            ? "sticky top-0 h-[100svh] w-full overflow-hidden"
            : "absolute inset-0 h-full w-full overflow-hidden"
        }
      >
        <video
          key={base}
          ref={videoRef}
          className="absolute inset-0 h-full w-full object-cover"
          poster="/hero-poster.webp"
          muted
          playsInline
          preload="metadata"
          aria-hidden
        >
          <source src={`/${base}.mp4`} type="video/mp4" />
        </video>

        {/* Light scrim — keeps the headline legible without dimming the film. */}
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-bg/85 via-bg/15 to-bg/35" />

        {/* Overlay: brand, headline, CTAs. */}
        <div className="absolute inset-0 flex items-end pb-[14vh] md:items-center md:pb-0">
          <div className="container-site">
            <div ref={overlayRef} className="max-w-3xl">
              <div className="hero-line inline-flex items-center gap-2 rounded-full border border-line-strong bg-bg/40 px-4 py-1.5 text-sm text-ink-soft backdrop-blur">
                <IconWave className="h-3.5 w-6 text-accent" />
                {t.footer.tagline}
              </div>
              <h1 className="hero-line mt-5 font-display text-4xl font-bold leading-tight text-ink sm:text-5xl md:text-6xl md:leading-[1.08]">
                <span className="text-shine">{t.hero.title}</span>
              </h1>
              <div className="hero-line mt-8 flex flex-col gap-3 sm:flex-row sm:flex-wrap">
                <a
                  href="/book"
                  className="cta-glow rounded-xl bg-accent px-6 py-3 text-center font-semibold text-bg transition-all duration-200 hover:-translate-y-0.5 hover:bg-accent-strong"
                >
                  {t.finalCta.button}
                </a>
                <a
                  href="#about"
                  className="rounded-xl border border-line-strong bg-bg/30 px-6 py-3 text-center font-semibold text-ink backdrop-blur transition-colors hover:border-accent hover:text-accent"
                >
                  {t.nav.about}
                </a>
              </div>
            </div>
          </div>
        </div>

        {/* Scroll hint (desktop scrub only). */}
        {scrub && (
          <div
            className="pointer-events-none absolute bottom-6 left-1/2 -translate-x-1/2 animate-bounce text-2xl leading-none text-ink-soft/70"
            aria-hidden
          >
            ⌄
          </div>
        )}
      </div>
    </section>
  );
}

function IconWave({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 24" className={className} aria-hidden fill="none">
      {[4, 11, 18, 25, 32, 39].map((x, i) => {
        const h = [10, 18, 6, 22, 12, 16][i];
        return (
          <rect key={x} x={x} y={12 - h / 2} width="4" height={h} rx="2" fill="currentColor" />
        );
      })}
    </svg>
  );
}
