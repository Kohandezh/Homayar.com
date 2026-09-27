import type { ReactNode } from "react";
import { Icon } from "./Icon";

export function CheckList({ items, render }: { items: string[]; render?: (item: string) => ReactNode }) {
  return (
    <ul className="flex flex-col gap-3" data-reveal-group>
      {items.map((item) => (
        <li key={item} className="flex items-start gap-3">
          <span className="mt-1.5 inline-grid size-5 shrink-0 place-items-center rounded-full border border-teal/40 bg-teal/10 text-teal">
            <Icon name="check" size={12} strokeWidth={2.5} />
          </span>
          <span className="text-ink-2">{render ? render(item) : item}</span>
        </li>
      ))}
    </ul>
  );
}

export function Chip({ children, tone = "default", live = false }: { children: ReactNode; tone?: "default" | "amber" | "teal" | "ember" | "solid"; live?: boolean }) {
  const cls = tone === "default" ? "" : `chip-${tone}`;
  return (
    <span className={`chip ${cls}`}>
      {live ? <span className="dot dot-live" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}

export function Notice({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div role="note" className="panel border-amber/40 p-5 md:p-6" data-reveal>
      <p className="mb-2 flex items-center gap-2 font-semibold text-amber-text">
        <Icon name="alert" size={18} />
        {title}
      </p>
      <div className="text-ink-2">{children}</div>
    </div>
  );
}

export function Numeral({ n }: { n: string }) {
  return (
    <span className="mono text-[11px] font-semibold text-ink-3" aria-hidden="true">
      {n}
    </span>
  );
}
