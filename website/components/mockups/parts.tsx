import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { localizeDigits } from "@/lib/i18n";
import { Icon } from "@/components/ui/Icon";

type Mock = Dictionary["mock"];

export function KpiGrid({ mock, compact = false }: { mock: Mock; compact?: boolean }) {
  return (
    <div className={`grid gap-2 ${compact ? "grid-cols-2" : "grid-cols-2 sm:grid-cols-4"}`}>
      {mock.kpis.map((k, i) => (
        <div key={k.label} className="rounded-lg border border-border-2 bg-bg-2 p-2.5">
          <div className="mb-1 flex items-center justify-between text-[10px] text-ink-3">
            <span className="truncate">{k.label}</span>
            <span className={i === 1 ? "text-accent-text" : i === 2 ? "text-accent-text" : "text-accent-text"}>
              <Icon name={i === 0 ? "phone" : i === 1 ? "activity" : i === 2 ? "alert" : "trend"} size={12} />
            </span>
          </div>
          <div className="flex items-end gap-1">
            <span className="label text-base font-bold leading-none">{k.value}</span>
            {"unit" in k && k.unit ? <span className="text-[10px] text-ink-3">{k.unit}</span> : null}
          </div>
        </div>
      ))}
    </div>
  );
}

export function TrendChart({ mock }: { mock: Mock }) {
  const points = [58, 62, 60, 66, 71, 69, 74, 78];
  const w = 240;
  const h = 70;
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${(i / (points.length - 1)) * w},${h - ((p - 50) / 35) * h}`).join(" ");
  return (
    <div className="rounded-lg border border-border-2 bg-bg-2 p-2.5">
      <p className="mb-1 text-[10px] font-semibold text-ink-3">{mock.trend}</p>
      <svg viewBox={`0 0 ${w} ${h + 4}`} className="h-16 w-full" preserveAspectRatio="none">
        <defs>
          <linearGradient id="trendFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="var(--color-accent)" stopOpacity="0.35" />
            <stop offset="1" stopColor="var(--color-accent)" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={`${path} L${w},${h + 4} L0,${h + 4} Z`} fill="url(#trendFill)" />
        <path d={path} fill="none" stroke="var(--color-accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}

export function SentimentBars({ mock }: { mock: Mock }) {
  const values = [54, 31, 15];
  const colors = ["bg-accent", "bg-accent", "bg-teal-deep"];
  return (
    <div className="rounded-lg border border-border-2 bg-bg-2 p-2.5">
      <p className="mb-2 text-[10px] font-semibold text-ink-3">{mock.sentiment}</p>
      <div className="flex flex-col gap-1.5">
        {mock.sentimentLabels.map((label, i) => (
          <div key={label} className="flex items-center gap-2 text-[10px]">
            <span className="w-12 shrink-0 text-ink-2">{label}</span>
            <div className="score-track flex-1">
              <div className={`h-full rounded-full ${colors[i]}`} style={{ width: `${values[i]}%` }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function ScoreBars({ mock, locale, limit }: { mock: Mock; locale: Locale; limit?: number }) {
  const rows = limit ? mock.scores.slice(0, limit) : mock.scores;
  return (
    <div className="flex flex-col gap-2">
      {rows.map((s, i) => (
        <div key={s.label} className="flex items-center gap-2 text-[11px]">
          <span className={`w-24 shrink-0 truncate ${i === 0 ? "font-semibold text-ink" : "text-ink-2"}`}>{s.label}</span>
          <div className="score-track flex-1">
            <div className="score-fill" style={{ width: `${s.value}%`, background: s.value < 75 ? "var(--color-teal-deep)" : undefined }} />
          </div>
          <span className="label w-6 text-end font-semibold">{localizeDigits(s.value, locale)}</span>
        </div>
      ))}
    </div>
  );
}

export function RootCauses({ mock }: { mock: Mock }) {
  return (
    <div className="flex flex-col gap-1.5">
      {mock.rootCauses.map((r, i) => (
        <div key={r.label} className="flex items-center justify-between gap-2 rounded-md bg-surface-2 px-2 py-1.5 text-[11px]">
          <span className="truncate">{r.label}</span>
          <span className={`chip shrink-0 !min-h-0 !px-1.5 !py-0 !text-[10px] ${i === 0 ? "chip-strong" : "chip-accent"}`}>{r.severity}</span>
        </div>
      ))}
    </div>
  );
}

export function TranscriptLines({ mock, lines }: { mock: Mock; lines?: number }) {
  const rows = lines ? mock.transcriptLines.slice(0, lines) : mock.transcriptLines;
  return (
    <div className="flex flex-col gap-1.5 text-[11px]">
      {rows.map((l, i) => (
        <div key={i} className="flex gap-2">
          <span className={`w-14 shrink-0 font-semibold ${i % 2 === 1 ? "text-accent-text" : "text-accent-text"}`}>{l.who}</span>
          <span className="text-ink-2">{l.text}</span>
        </div>
      ))}
    </div>
  );
}

export function Stages({ mock }: { mock: Mock }) {
  const stateStyle = (i: number) => (i < 2 ? "chip-accent" : i === 2 ? "" : "chip-accent");
  return (
    <ol className="flex flex-col gap-1.5">
      {mock.stages.map((s, i) => (
        <li key={s} className="flex items-center justify-between gap-2 rounded-md border border-border-2 bg-bg-2 px-2.5 py-1.5 text-[11px]">
          <span className="flex items-center gap-2">
            <span className="label inline-grid size-4 place-items-center rounded-full bg-surface-2 text-[9px] font-bold">{i + 1}</span>
            {s}
          </span>
          <span className={`chip !min-h-0 !px-2 !py-0.5 !text-[10px] ${stateStyle(i)}`}>{mock.stageStates[i]}</span>
        </li>
      ))}
    </ol>
  );
}

export function CopilotChat({ mock }: { mock: Mock }) {
  return (
    <div className="flex flex-col gap-2 text-[11px]">
      <div className="ms-6 rounded-xl rounded-ee-sm bg-accent px-3 py-2 text-on-accent">{mock.copilotQuestion}</div>
      <div className="me-6 flex items-start gap-2 rounded-xl rounded-ss-sm border border-border-2 bg-bg-2 px-3 py-2">
        <span className="mt-0.5 text-accent-text">
          <Icon name="bot" size={14} />
        </span>
        <span className="text-ink-2">{mock.copilotAnswer}</span>
      </div>
    </div>
  );
}

export function UploadBox({ mock }: { mock: Mock }) {
  return (
    <div className="flex flex-col items-center gap-1.5 rounded-xl border border-dashed border-accent/50 bg-accent/5 p-4 text-center">
      <span className="text-accent-text">
        <Icon name="upload" size={20} />
      </span>
      <span className="text-[11px] font-semibold">{mock.upload}</span>
      <span className="label text-[10px] text-ink-3" dir="ltr">
        {mock.uploadFormats}
      </span>
    </div>
  );
}
