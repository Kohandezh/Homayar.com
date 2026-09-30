import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { localizeDigits } from "@/lib/i18n";
import { bidi } from "@/lib/bidi";
import { Icon } from "@/components/ui/Icon";
import { CopilotChat, KpiGrid, RootCauses, ScoreBars, Stages, TranscriptLines, TrendChart, UploadBox } from "@/components/mockups/parts";

const navIcons = ["layout", "phone", "alert", "trend", "bot", "layers", "lock"];
const slotAreas = ["ingest", "transcript", "agents", "dashboard", "report", "copilot", "integ"];
const agentIcons = ["gauge", "alert", "trend", "book"];

export function ConsoleAssembly({ dict, locale }: { dict: Dictionary; locale: Locale }) {
  const p = dict.product;
  const s = p.sections;
  const c = p.console;
  const m = dict.mock;
  const t = (text: string) => bidi(text, locale);
  const docks = [s.ingest, s.transcription, s.agents, s.dashboard, s.reports, s.copilot, s.admin];

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
      {[m.webhookTitle, "LDAP · AD", m.roles.join(" · "), m.exportPdf].map((x) => (
        <li key={x} className="chip !min-h-0 !px-2 !py-1 !text-[10px]" dir="auto">
          {x}
        </li>
      ))}
    </ul>,
  ];

  return (
    <section className="console relative" data-console data-testid="console">
      <div className="console-pin">
        <div className="container-x console-grid">
          <div className="console-texts">
            <div className="console-texts-head">
              <p className="eyebrow">{c.eyebrow}</p>
              <h2 className="display-2 mt-3">{c.title}</h2>
              <p className="mt-3 text-ink-2">{c.text}</p>
              <p className="chip mt-4 console-hint">
                <Icon name="scan" size={13} />
                {c.hint}
              </p>
            </div>
            <ol className="console-text-list">
              {docks.map((d, i) => (
                <li key={d.id} className="console-text" data-dock-text data-testid="console-text">
                  <div className="flex items-center gap-2">
                    <span className="label text-xs text-accent-text">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
                    <span className="chip !text-[11px]">{d.label}</span>
                  </div>
                  <h3 className="mt-3 text-xl font-semibold md:text-2xl">{t(d.title)}</h3>
                  {"text" in d ? <p className="mt-2 text-ink-2">{t(d.text)}</p> : null}
                  {"bullets" in d ? (
                    <ul className="mt-3 flex flex-col gap-1.5 text-sm text-ink-2">
                      {d.bullets.slice(0, 2).map((b) => (
                        <li key={b} className="flex gap-2">
                          <span className="mt-2 size-1.5 shrink-0 rounded-full bg-accent" />
                          {t(b)}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              ))}
            </ol>
          </div>

          <div className="console-window panel panel-glow" role="img" aria-label={dict.common.decorativeMockup} data-console-window>
            <div className="panel-head" aria-hidden="true">
              <span className="window-dots">
                <span />
                <span />
                <span />
              </span>
              <span className="label ms-3 rounded-md bg-surface px-2 py-0.5 text-[10px] text-ink-3" dir="ltr">
                app.homayar.local
              </span>
              <span className="chip chip-accent ms-auto !min-h-0 !px-2 !py-0.5 !text-[10px]" data-console-status>
                {c.complete}
              </span>
            </div>
            <div className="console-body" aria-hidden="true">
              <aside className="console-nav">
                {m.nav.map((n, i) => (
                  <span key={n} className={`flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[10px] ${i === 0 ? "bg-accent/12 font-semibold text-accent-text" : "text-ink-2"}`}>
                    <Icon name={navIcons[i]} size={12} />
                    <span className="truncate">{n}</span>
                  </span>
                ))}
              </aside>
              <div className="console-slots">
                {docks.map((d, i) => (
                  <div key={d.id} className="console-slot" data-slot={i} style={{ gridArea: slotAreas[i] }}>
                    <span className="console-slot-label">
                      <span className="console-slot-num">{localizeDigits(i + 1, locale)}</span>
                      {d.label}
                      <span className="console-slot-state console-slot-waiting">{c.waiting}</span>
                      <span className="console-slot-state console-slot-docked">{c.docked}</span>
                    </span>
                    <div className="console-panel" data-dock={i} data-testid="console-dock">
                      {panels[i]}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
