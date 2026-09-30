"use client";

import { useEffect, useRef } from "react";

function spline(points: Array<[number, number]>): string {
  if (points.length < 2) return "";
  const p = points;
  let d = `M${p[0][0].toFixed(1)} ${p[0][1].toFixed(1)}`;
  for (let i = 0; i < p.length - 1; i++) {
    const [x1, y1] = p[i];
    const [x2, y2] = p[i + 1];
    const dy = (y2 - y1) * 0.55;
    d += ` C${x1.toFixed(1)} ${(y1 + dy).toFixed(1)} ${x2.toFixed(1)} ${(y2 - dy).toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`;
  }
  return d;
}

export function SnakePath() {
  const ref = useRef<SVGSVGElement>(null);

  useEffect(() => {
    const svg = ref.current;
    const section = svg?.closest<HTMLElement>("[data-snake]");
    if (!svg || !section) return;
    let frame = 0;
    const build = () => {
      frame = 0;
      const box = section.getBoundingClientRect();
      const cards = Array.from(section.querySelectorAll<HTMLElement>("[data-snake-card]"));
      if (cards.length === 0) return;
      const pts: Array<[number, number]> = [];
      const first = cards[0].getBoundingClientRect();
      pts.push([first.left + first.width / 2 - box.left, 0]);
      for (const c of cards) {
        const r = c.getBoundingClientRect();
        pts.push([r.left + r.width / 2 - box.left, r.top + r.height / 2 - box.top]);
      }
      const last = cards[cards.length - 1].getBoundingClientRect();
      const lastX = last.left + last.width / 2 - box.left;
      pts.push([box.width - lastX, box.height]);
      const d = spline(pts);
      svg.setAttribute("viewBox", `0 0 ${Math.round(box.width)} ${Math.round(box.height)}`);
      svg.querySelectorAll("path").forEach((p) => p.setAttribute("d", d));
      section.dispatchEvent(new CustomEvent("snake:update"));
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
    <svg ref={ref} className="snake-svg" aria-hidden="true" focusable="false" preserveAspectRatio="none" data-testid="snake-svg">
      <path className="snake-glow" d="" />
      <path className="snake-line" d="" data-snake-line />
      <circle className="snake-head-glow" r="22" cx="-100" cy="-100" />
      <circle className="snake-head" r="10" cx="-100" cy="-100" data-snake-head />
    </svg>
  );
}
