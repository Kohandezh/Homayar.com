import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { bidi } from "@/lib/bidi";
import { Icon } from "@/components/ui/Icon";
import { CopilotChat, KpiGrid, RootCauses, ScoreBars, Stages, TranscriptLines, TrendChart, UploadBox } from "@/components/mockups/parts";

const agentIcons = ["gauge", "alert", "trend", "book"];

// The product screen for each of the seven parts, in the order of p.sections.
export function dockPanels(dict: Dictionary, locale: Locale) {
  const s = dict.product.sections;
  const m = dict.mock;
  const t = (text: string) => bidi(text, locale);
  const panels = [
    <div key="ingest" className="flex flex-col gap-2">
      <UploadBox mock={m} />
    </div>,
    <div key="transcript" className="flex flex-col gap-2">
      <Stages mock={m} />
      <div className="rounded-lg border border-border-2 bg-bg-2 p-2">
        <TranscriptLines mock={m} lines={2} />
      </div>
    </div>,
    <ol key="agents" className="flex flex-col gap-1.5">
      {s.agents.items.map((a, i) => (
        <li key={a.title} className="flex items-center gap-2 rounded-md border border-border-2 bg-bg-2 px-2 py-1.5 text-[11px]">
          <Icon name={agentIcons[i]} size={12} className="text-accent-text" />
          <span className="truncate font-semibold">{t(a.title)}</span>
          <span className="chip chip-accent ms-auto !min-h-0 !px-1.5 !py-0 !text-[10px]">{m.stageStates[0]}</span>
        </li>
      ))}
    </ol>,
    <div key="dashboard" className="flex flex-col gap-2">
      <KpiGrid mock={m} />
      <TrendChart mock={m} />
    </div>,
    <div key="report" className="flex flex-col gap-2">
      <div className="rounded-lg border border-border-2 bg-bg-2 p-2">
        <p className="mb-1.5 text-[10px] font-semibold text-ink-3">{m.qc}</p>
        <ScoreBars mock={m} locale={locale} limit={4} />
      </div>
      <div className="rounded-lg border border-border-2 bg-bg-2 p-2">
        <p className="mb-1.5 text-[10px] font-semibold text-ink-3">{m.problems}</p>
        <RootCauses mock={m} />
      </div>
    </div>,
    <div key="copilot">
      <CopilotChat mock={m} />
    </div>,
    <ul key="integ" className="flex flex-wrap items-center gap-2 text-[11px]">
      {[m.webhookTitle, "Google OAuth", m.roles.join(" · "), m.exportPdf].map((x) => (
        <li key={x} className="chip !min-h-0 !px-2 !py-1 !text-[10px]" dir="auto">
          {x}
        </li>
      ))}
    </ul>,
  ];
  return panels;
}
