import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { Icon } from "@/components/ui/Icon";
import { KpiGrid, RootCauses, ScoreBars, SentimentBars, TranscriptLines, TrendChart } from "./parts";

const navIcons = ["layout", "phone", "alert", "trend", "bot", "layers", "lock"];

/**
 * A desktop console window: sidebar, dashboard KPIs and a call report.
 * Purely decorative; the visible labels match the real console.
 */
export function AppWindow({ dict, locale, label }: { dict: Dictionary; locale: Locale; label: string }) {
  const m = dict.mock;
  return (
    <div className="mock-window w-full" role="img" aria-label={label}>
      <div className="flex items-center gap-2 border-b border-border bg-sand px-3 py-2" aria-hidden="true">
        <span className="size-2.5 rounded-full bg-terracotta/70" />
        <span className="size-2.5 rounded-full bg-accent/80" />
        <span className="size-2.5 rounded-full bg-primary/70" />
        <span className="ms-3 rounded-md bg-surface px-2 py-0.5 text-[10px] text-ink-2" dir="ltr">
          app.homayar.local/overview
        </span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-[92px_1fr]" aria-hidden="true">
        <aside className="hidden flex-col gap-1 bg-ink-teal p-2 text-cream sm:flex">
          <div className="mb-2 flex items-center gap-1.5 px-1.5 py-1">
            <span className="inline-grid size-5 place-items-center rounded bg-[#42c7b1] text-ink-teal">
              <Icon name="activity" size={12} strokeWidth={2.5} />
            </span>
            <span className="text-[11px] font-bold">{dict.brand}</span>
          </div>
          {m.nav.map((n, i) => (
            <span
              key={n}
              className={`flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[10px] ${i === 0 ? "bg-[#42c7b1]/20 font-semibold" : "text-cream/75"}`}
            >
              <Icon name={navIcons[i]} size={12} />
              <span className="truncate">{n}</span>
            </span>
          ))}
        </aside>
        <div className="flex flex-col gap-2.5 bg-bg p-3">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold">{m.workspace}</span>
            <span className="rounded-full bg-surface px-2 py-0.5 text-[10px] text-ink-2">{m.nav[0]}</span>
          </div>
          <KpiGrid mock={m} />
          <div className="grid grid-cols-2 gap-2">
            <TrendChart mock={m} />
            <SentimentBars mock={m} />
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-[1.1fr_1fr]">
            <div className="rounded-lg border border-border bg-surface p-2.5">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[11px] font-semibold">{m.report}</span>
                <span className="inline-flex items-center gap-1 rounded-full bg-sand px-2 py-0.5 text-[10px]">
                  <Icon name="download" size={10} />
                  {m.exportPdf}
                </span>
              </div>
              <p className="mb-1.5 text-[10px] font-semibold text-ink-2">{m.qc}</p>
              <ScoreBars mock={m} locale={locale} limit={5} />
            </div>
            <div className="flex flex-col gap-2">
              <div className="rounded-lg border border-border bg-surface p-2.5">
                <p className="mb-1.5 text-[10px] font-semibold text-ink-2">{m.problems}</p>
                <RootCauses mock={m} />
              </div>
              <div className="rounded-lg border border-border bg-surface p-2.5">
                <p className="mb-1.5 text-[10px] font-semibold text-ink-2">{m.transcript}</p>
                <TranscriptLines mock={m} lines={2} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
