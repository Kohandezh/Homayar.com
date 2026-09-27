import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { Icon } from "@/components/ui/Icon";
import { KpiGrid, RootCauses, ScoreBars, SentimentBars, TranscriptLines, TrendChart } from "./parts";

const navIcons = ["layout", "phone", "alert", "trend", "bot", "layers", "lock"];

export function AppWindow({ dict, locale, label }: { dict: Dictionary; locale: Locale; label: string }) {
  const m = dict.mock;
  return (
    <div className="panel panel-glow w-full overflow-hidden" role="img" aria-label={label}>
      <div className="panel-head" aria-hidden="true">
        <span className="window-dots">
          <span />
          <span />
          <span />
        </span>
        <span className="mono ms-3 rounded-md bg-surface px-2 py-0.5 text-[10px] text-ink-3" dir="ltr">
          app.homayar.local/overview
        </span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-[96px_1fr]" aria-hidden="true">
        <aside className="hidden flex-col gap-1 border-e border-border-2 bg-bg-2 p-2 sm:flex">
          <div className="mb-2 flex items-center gap-1.5 px-1.5 py-1">
            <span className="inline-grid size-5 place-items-center rounded bg-amber text-on-amber">
              <Icon name="activity" size={12} strokeWidth={2.5} />
            </span>
            <span className="text-[11px] font-bold">{dict.brand}</span>
          </div>
          {m.nav.map((n, i) => (
            <span key={n} className={`flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[10px] ${i === 0 ? "bg-amber/12 font-semibold text-amber-text" : "text-ink-2"}`}>
              <Icon name={navIcons[i]} size={12} />
              <span className="truncate">{n}</span>
            </span>
          ))}
        </aside>
        <div className="flex flex-col gap-2.5 bg-surface p-3">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold">{m.workspace}</span>
            <span className="chip chip-teal !min-h-0 !px-2 !py-0.5 !text-[10px]">
              <span className="dot dot-live" />
              {m.nav[0]}
            </span>
          </div>
          <KpiGrid mock={m} />
          <div className="grid grid-cols-2 gap-2">
            <TrendChart mock={m} />
            <SentimentBars mock={m} />
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-[1.1fr_1fr]">
            <div className="rounded-lg border border-border-2 bg-bg-2 p-2.5">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[11px] font-semibold">{m.report}</span>
                <span className="chip !min-h-0 !px-2 !py-0.5 !text-[10px]">
                  <Icon name="download" size={10} />
                  {m.exportPdf}
                </span>
              </div>
              <p className="mb-1.5 text-[10px] font-semibold text-ink-3">{m.qc}</p>
              <ScoreBars mock={m} locale={locale} limit={5} />
            </div>
            <div className="flex flex-col gap-2">
              <div className="rounded-lg border border-border-2 bg-bg-2 p-2.5">
                <p className="mb-1.5 text-[10px] font-semibold text-ink-3">{m.problems}</p>
                <RootCauses mock={m} />
              </div>
              <div className="rounded-lg border border-border-2 bg-bg-2 p-2.5">
                <p className="mb-1.5 text-[10px] font-semibold text-ink-3">{m.transcript}</p>
                <TranscriptLines mock={m} lines={2} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
