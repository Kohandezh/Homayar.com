"use client";

import { useEffect, useState } from "react";

export function SectionTabs({ items, label }: { items: { id: string; label: string }[]; label: string }) {
  const [active, setActive] = useState(items[0]?.id ?? "");

  useEffect(() => {
    const targets = items.map((i) => document.getElementById(i.id)).filter((el): el is HTMLElement => !!el);
    if (targets.length === 0) return;
    const io = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { rootMargin: "-40% 0px -50% 0px", threshold: 0 },
    );
    targets.forEach((t) => io.observe(t));
    return () => io.disconnect();
  }, [items]);

  return (
    <nav aria-label={label} className="sticky top-[var(--header-h)] z-30 border-b border-border bg-bg/80 backdrop-blur-md">
      <div className="container-x -mx-0 overflow-x-auto">
        <ul className="flex w-max gap-1 py-2">
          {items.map((i) => (
            <li key={i.id}>
              <a
                href={`#${i.id}`}
                aria-current={active === i.id ? "true" : undefined}
                className={`label inline-flex min-h-11 items-center rounded-full px-4 text-xs font-semibold transition-colors ${
                  active === i.id ? "bg-accent/12 text-accent-text" : "text-ink-2 hover:bg-surface-2 hover:text-ink"
                }`}
              >
                {i.label}
              </a>
            </li>
          ))}
        </ul>
      </div>
    </nav>
  );
}
