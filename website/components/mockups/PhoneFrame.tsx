import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { Icon } from "@/components/ui/Icon";
import { ScoreBars } from "./parts";

const tabIcons = ["layout", "phone", "alert", "bot"];

export function PhoneFrame({ dict, locale, label, className = "" }: { dict: Dictionary; locale: Locale; label: string; className?: string }) {
  const m = dict.mock;
  return (
    <div className={`w-[220px] rounded-[40px] border border-border-2 bg-bg-2 p-2.5 shadow-[0_40px_80px_-30px_rgba(245,158,11,0.35)] ${className}`} role="img" aria-label={label}>
      <div className="flex h-[440px] flex-col overflow-hidden rounded-[32px] bg-bg" aria-hidden="true">
        <div className="mono flex items-center justify-between px-4 pt-3 text-[10px] font-semibold">
          <span>9:41</span>
          <span className="h-4 w-14 rounded-full bg-surface-2" />
          <span className="flex gap-0.5">
            <span className="h-2 w-1 rounded-sm bg-ink" />
            <span className="h-2 w-1 rounded-sm bg-ink" />
            <span className="h-2 w-1 rounded-sm bg-ink/40" />
          </span>
        </div>
        <div className="flex items-center justify-between px-3 pt-3">
          <span className="text-sm font-bold">{m.phoneTitle}</span>
          <span className="inline-grid size-7 place-items-center rounded-full bg-amber text-on-amber">
            <Icon name="upload" size={14} />
          </span>
        </div>
        <div className="flex flex-1 flex-col gap-2 overflow-hidden px-3 pt-2">
          {m.phoneItems.map((item, i) => (
            <div key={item.title} className={`rounded-xl border p-2.5 ${i === 0 ? "border-amber/50 bg-surface" : "border-border-2 bg-surface/60"}`}>
              <div className="flex items-center justify-between text-[11px]">
                <span className="font-semibold">{item.title}</span>
                <span className="mono text-ink-3">{item.meta}</span>
              </div>
              {i === 0 ? (
                <div className="mt-2">
                  <p className="mb-1 text-[10px] font-semibold text-ink-3">{m.qc}</p>
                  <ScoreBars mock={m} locale={locale} limit={3} />
                </div>
              ) : null}
            </div>
          ))}
          <div className="mt-auto flex items-center gap-2 rounded-xl border border-border-2 bg-surface p-2.5 text-[10px] text-ink-2">
            <Icon name="bot" size={14} className="text-teal" />
            <span className="truncate">{m.copilotQuestion}</span>
          </div>
        </div>
        <div className="mt-2 grid grid-cols-4 border-t border-border-2 bg-bg-2 px-1 pb-3 pt-1.5">
          {m.phoneTabs.map((t, i) => (
            <span key={t} className={`flex flex-col items-center gap-0.5 text-[9px] font-semibold ${i === 1 ? "text-amber-text" : "text-ink-3"}`}>
              <Icon name={tabIcons[i]} size={16} />
              {t}
            </span>
          ))}
          <span className="col-span-4 mt-1.5 h-1 w-20 justify-self-center rounded-full bg-ink/60" />
        </div>
      </div>
    </div>
  );
}
