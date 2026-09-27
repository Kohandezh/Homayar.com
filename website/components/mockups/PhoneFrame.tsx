import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { Icon } from "@/components/ui/Icon";
import { ScoreBars } from "./parts";

const tabIcons = ["layout", "phone", "alert", "bot"];

/** A phone showing the call list and one report. Decorative. */
export function PhoneFrame({ dict, locale, label, className = "" }: { dict: Dictionary; locale: Locale; label: string; className?: string }) {
  const m = dict.mock;
  return (
    <div className={`mock-phone w-[220px] ${className}`} role="img" aria-label={label}>
      <div className="mock-phone-screen flex h-[440px] flex-col" aria-hidden="true">
        <div className="flex items-center justify-between px-4 pt-3 text-[10px] font-semibold">
          <span>9:41</span>
          <span className="h-4 w-14 rounded-full bg-ink-teal" />
          <span className="flex gap-0.5">
            <span className="h-2 w-1 rounded-sm bg-ink" />
            <span className="h-2 w-1 rounded-sm bg-ink" />
            <span className="h-2 w-1 rounded-sm bg-ink/40" />
          </span>
        </div>
        <div className="flex items-center justify-between px-3 pt-3">
          <span className="text-sm font-bold">{m.phoneTitle}</span>
          <span className="inline-grid size-7 place-items-center rounded-full bg-surface text-primary">
            <Icon name="upload" size={14} />
          </span>
        </div>
        <div className="flex flex-1 flex-col gap-2 overflow-hidden px-3 pt-2">
          {m.phoneItems.map((item, i) => (
            <div
              key={item.title}
              className={`rounded-xl border p-2.5 ${i === 0 ? "border-primary bg-surface" : "border-border bg-surface/70"}`}
            >
              <div className="flex items-center justify-between text-[11px]">
                <span className="font-semibold">{item.title}</span>
                <span className="text-ink-2">{item.meta}</span>
              </div>
              {i === 0 ? (
                <div className="mt-2">
                  <p className="mb-1 text-[10px] font-semibold text-ink-2">{m.qc}</p>
                  <ScoreBars mock={m} locale={locale} limit={3} />
                </div>
              ) : null}
            </div>
          ))}
          <div className="mt-auto flex items-center gap-2 rounded-xl bg-ink-teal p-2.5 text-[10px] text-cream">
            <Icon name="bot" size={14} />
            <span className="truncate">{m.copilotQuestion}</span>
          </div>
        </div>
        <div className="mt-2 grid grid-cols-4 border-t border-border bg-surface px-1 pb-3 pt-1.5">
          {m.phoneTabs.map((t, i) => (
            <span key={t} className={`flex flex-col items-center gap-0.5 text-[9px] font-semibold ${i === 1 ? "text-primary" : "text-ink-2"}`}>
              <Icon name={tabIcons[i]} size={16} />
              {t}
            </span>
          ))}
          <span className="col-span-4 mt-1.5 h-1 w-20 justify-self-center rounded-full bg-ink/70" />
        </div>
      </div>
    </div>
  );
}
