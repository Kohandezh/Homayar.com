"use client";

import { useEffect, useRef } from "react";

export function ConstellationSvg() {
  const ref = useRef<SVGSVGElement>(null);

  useEffect(() => {
    const svg = ref.current;
    const section = svg?.closest<HTMLElement>("[data-constellation]");
    if (!svg || !section) return;
    let frame = 0;
    const build = () => {
      frame = 0;
      const box = section.getBoundingClientRect();
      const dots = Array.from(section.querySelectorAll<HTMLElement>("[data-star-dot]"));
      if (dots.length < 2) return;
      const pts = dots.map((d) => {
        const r = d.getBoundingClientRect();
        return [r.left + r.width / 2 - box.left, r.top + r.height / 2 - box.top] as const;
      });
      const chain = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join("");
      const extra = pts.length >= 6 ? `M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}L${pts[4][0].toFixed(1)} ${pts[4][1].toFixed(1)}M${pts[1][0].toFixed(1)} ${pts[1][1].toFixed(1)}L${pts[5][0].toFixed(1)} ${pts[5][1].toFixed(1)}` : "";
      svg.setAttribute("viewBox", `0 0 ${Math.round(box.width)} ${Math.round(box.height)}`);
      svg.querySelector("[data-const-links]")?.setAttribute("d", chain + extra);
      const stars = svg.querySelector("[data-const-stars]");
      if (stars) {
        stars.innerHTML = pts
          .map((p) => `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="16" class="const-halo"/><circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="4" class="const-core"/>`)
          .join("");
      }
      section.dispatchEvent(new CustomEvent("constellation:update"));
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(build);
    };
    build();
    const ro = new ResizeObserver(schedule);
    ro.observe(section);
    document.fonts?.ready.then(schedule).catch(() => undefined);
    return () => {
      ro.disconnect();
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <svg ref={ref} className="const-svg" aria-hidden="true" focusable="false" preserveAspectRatio="none" data-testid="constellation-svg">
      <path className="const-links" d="" data-const-links />
      <g data-const-stars />
    </svg>
  );
}
