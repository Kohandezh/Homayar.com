import type { Dictionary } from "@/i18n/en";
import { localizeDigits, type Locale } from "@/lib/i18n";

// Data drawn with the world's material (dots, lines, glow), with no boxes around it.
// See design-system/SCENE-RULES.md.

type Mock = Dictionary["mock"];

export function NakedScores({ mock, locale, limit }: { mock: Mock; locale: Locale; limit?: number }) {
  const rows = limit ? mock.scores.slice(0, limit) : mock.scores;
  return (
    <div className="flex flex-col gap-3">
      {rows.map((s, i) => (
        <div key={s.label} className="grid grid-cols-[6.5rem_1fr_2rem] items-center gap-3 text-sm">
          <span className={i === 0 ? "font-semibold text-ink" : "text-ink-2"}>{s.label}</span>
          <span className="dot-track">
            <span className="dot-fill" style={{ width: `${s.value}%` }} />
          </span>
          <span className="label text-end font-bold text-accent-text">{localizeDigits(s.value, locale)}</span>
        </div>
      ))}
    </div>
  );
}

export function NakedCauses({ mock }: { mock: Mock }) {
  return (
    <ul className="flex flex-col gap-3">
      {mock.rootCauses.map((r, i) => (
        <li key={r.label} className="flex items-center gap-3 text-sm">
          <span className={`glow-dot ${i === 0 ? "glow-dot-strong" : ""}`} aria-hidden="true" />
          <span className="flex-1 text-ink">{r.label}</span>
          <span className="label text-xs font-semibold text-accent-text">{r.severity}</span>
        </li>
      ))}
    </ul>
  );
}

export function NakedFigures({ items }: { items: ReadonlyArray<{ label: string; value: string }> }) {
  return (
    <dl className="grid grid-cols-3 gap-4">
      {items.map((it) => (
        <div key={it.label} className="flex flex-col gap-1">
          <dt className="text-xs text-ink-3">{it.label}</dt>
          <dd className="text-xl font-bold text-accent-text">{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function NakedSteps({ items, locale }: { items: ReadonlyArray<string>; locale: Locale }) {
  return (
    <ol className="naked-steps flex flex-col gap-4">
      {items.map((it, i) => (
        <li key={it} className="relative flex items-start gap-3 text-sm text-ink-2">
          <span className="label w-5 shrink-0 font-bold text-accent-text">{localizeDigits(i + 1, locale)}</span>
          {it}
        </li>
      ))}
    </ol>
  );
}

export function NakedList({ items, render, tone = "accent" }: { items: ReadonlyArray<string>; render?: (item: string) => React.ReactNode; tone?: "accent" | "muted" }) {
  return (
    <ul className="flex flex-col gap-3" data-reveal-group>
      {items.map((item) => (
        <li key={item} className="flex items-start gap-3 text-ink-2">
          <span className={`glow-dot mt-2 ${tone === "muted" ? "opacity-50" : ""}`} aria-hidden="true" />
          <span>{render ? render(item) : item}</span>
        </li>
      ))}
    </ul>
  );
}
