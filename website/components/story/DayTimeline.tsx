import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { localizeDigits } from "@/lib/i18n";
import { bidi } from "@/lib/bidi";
import type { SolutionSlug } from "@/lib/site";
import { Icon } from "@/components/ui/Icon";
import { FlowDiagram } from "@/components/story/FlowDiagram";
import { CopilotChat, KpiGrid, RootCauses, ScoreBars, SentimentBars, TrendChart } from "@/components/mockups/parts";

type Mock = Dictionary["mock"];

function MiniScreen({ kind, dict, locale }: { kind: string; dict: Dictionary; locale: Locale }) {
  const m: Mock = dict.mock;
  const d = dict.demo;
  switch (kind) {
    case "kpis":
      return <KpiGrid mock={m} compact />;
    case "report":
      return <ScoreBars mock={m} locale={locale} limit={4} />;
    case "pdf":
      return (
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between text-[11px]">
            <span className="font-semibold">{m.report}</span>
            <span className="chip chip-accent !min-h-0 !px-2 !py-0.5 !text-[10px]">
              <Icon name="download" size={10} />
              {m.exportPdf}
            </span>
          </div>
          <ScoreBars mock={m} locale={locale} limit={2} />
        </div>
      );
    case "training":
      return (
        <ol className="flex flex-col gap-1.5">
          {d.agents.teacher.items.slice(0, 2).map((it, i) => (
            <li key={it} className="flex items-start gap-2 rounded-md border border-border-2 bg-bg-2 p-2 text-[11px] text-ink-2">
              <span className="label inline-grid size-4 shrink-0 place-items-center rounded-full bg-accent text-[9px] text-on-accent">{localizeDigits(i + 1, locale)}</span>
              {it}
            </li>
          ))}
        </ol>
      );
    case "trend":
      return <TrendChart mock={m} />;
    case "sentiment":
      return <SentimentBars mock={m} />;
    case "incidents":
    case "issues":
      return <RootCauses mock={m} />;
    case "compare":
      return (
        <div className="flex flex-col gap-1.5">
          {m.compare.map((label, i) => (
            <div key={label} className="flex items-center gap-2 text-[11px]">
              <span className="w-16 shrink-0 truncate text-ink-2">{label}</span>
              <div className="score-track flex-1">
                <div className="score-fill" style={{ width: `${[82, 71, 88][i]}%`, background: i === 1 ? "var(--color-teal-deep)" : undefined }} />
              </div>
              <span className="label w-6 text-end font-semibold">{localizeDigits([82, 71, 88][i], locale)}</span>
            </div>
          ))}
        </div>
      );
    case "copilot":
      return <CopilotChat mock={m} />;
    case "signals":
      return (
        <div className="grid grid-cols-3 gap-1.5">
          {d.agents.insights.items.map((it) => (
            <div key={it.label} className="rounded-md border border-border-2 bg-bg-2 p-2">
              <p className="text-[10px] text-ink-3">{it.label}</p>
              <p className="mt-1 text-xs font-bold">{it.value}</p>
            </div>
          ))}
        </div>
      );
    case "webhook":
      return (
        <div className="rounded-lg border border-border-2 bg-bg-2 p-2 text-[10px]">
          <p className="label mb-1 font-semibold text-ink-3">{m.webhookTitle}</p>
          <pre dir="ltr" className="overflow-x-auto rounded-md bg-bg p-2 text-[10px] leading-relaxed text-accent-text">{`POST /api/webhook/voip/call-event
X-Webhook-Secret: ••••••••`}</pre>
        </div>
      );
    case "ldap":
      return (
        <div className="flex flex-col gap-2 text-[11px]">
          <span className="chip chip-accent !min-h-0 w-fit !px-2 !py-0.5 !text-[10px]" dir="ltr">
            LDAP · AD
          </span>
          <div className="flex flex-wrap gap-1.5">
            {m.roles.map((r) => (
              <span key={r} className="chip !min-h-0 !px-2 !py-0.5 !text-[10px]">
                {r}
              </span>
            ))}
          </div>
        </div>
      );
    case "egress":
      return <FlowDiagram flow={dict.deployment.flow} mode="self-hosted" compact />;
    case "roles":
      return (
        <div className="flex flex-col gap-1.5 text-[11px]">
          <span className="chip chip-strong !min-h-0 w-fit !px-2 !py-0.5 !text-[10px]">{m.rls}</span>
          {m.roles.map((r, i) => (
            <div key={r} className="flex items-center justify-between rounded-md border border-border-2 bg-bg-2 px-2 py-1">
              <span>{r}</span>
              <span className="flex gap-1">
                {[0, 1, 2].map((k) => (
                  <span key={k} className={`size-2 rounded-full ${k <= 2 - i ? "bg-accent" : "bg-border-2"}`} />
                ))}
              </span>
            </div>
          ))}
        </div>
      );
    case "runbook":
      return (
        <ol className="flex flex-col gap-1">
          {m.runbook.map((r, i) => (
            <li key={r} className="flex items-center gap-2 rounded-md border border-border-2 bg-bg-2 px-2 py-1 text-[11px]">
              <span className="label inline-grid size-4 place-items-center rounded-full bg-surface-2 text-[9px] font-bold">{localizeDigits(i + 1, locale)}</span>
              {r}
              <Icon name="check" size={11} className="ms-auto text-accent-text" />
            </li>
          ))}
        </ol>
      );
    default:
      return null;
  }
}

export function DayTimeline({ dict, locale, slug }: { dict: Dictionary; locale: Locale; slug: SolutionSlug }) {
  const role = dict.solutions.roles[slug];
  const day = role.day;
  const t = (text: string) => bidi(text, locale);
  return (
    <section className="day relative" data-journey data-day data-testid="day">
      <div className="day-pin">
        <div className="container-x">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="eyebrow">{role.name}</p>
              <h2 className="display-2 mt-3">{day.title}</h2>
            </div>
            <p className="chip day-hint">
              <Icon name="scan" size={13} />
              {day.hint}
            </p>
          </div>
          <div className="day-axis mt-6" aria-hidden="true">
            <div className="day-axis-fill" data-journey-progress />
            {day.items.map((it) => (
              <span key={it.time} className="day-tick">
                <span className="day-tick-dot" />
                <span className="label day-tick-time">{it.time}</span>
              </span>
            ))}
          </div>
        </div>
        <ol className="day-track" data-journey-track>
          {day.items.map((it, i) => (
            <li key={it.time} className="day-card panel" data-journey-panel style={{ ["--k" as string]: i }} data-testid="day-card">
              <div className="flex items-center justify-between">
                <span className="chip chip-accent">
                  <Icon name="clock" size={12} />
                  {it.time}
                </span>
                <span className="label text-[11px] text-ink-3">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
              </div>
              <h3 className="mt-4 text-xl font-bold md:text-2xl">{t(it.task)}</h3>
              <p className="mt-2 text-sm text-ink-2 md:text-base">{t(it.text)}</p>
              <div className="mt-4 rounded-xl border border-border-2 bg-bg-deep p-3" aria-hidden="true">
                <MiniScreen kind={it.screen} dict={dict} locale={locale} />
              </div>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
