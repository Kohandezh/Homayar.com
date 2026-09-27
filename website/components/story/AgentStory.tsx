import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { localizeDigits } from "@/lib/i18n";
import { RootCauses, ScoreBars } from "@/components/mockups/parts";
import { Icon } from "@/components/ui/Icon";
import { SectionHeading } from "@/components/ui/Section";

export function AgentStory({ dict, locale }: { dict: Dictionary; locale: Locale }) {
  const items = dict.home.outputs.items;
  const d = dict.demo;
  const m = dict.mock;
  const label = dict.common.decorativeMockup;
  const cards = [
    <div key="qc" className="flex flex-col gap-3">
      <CardHead name={d.agents.qc.name} agent={d.agents.qc.agent} />
      <ScoreBars mock={m} locale={locale} />
      <p className="mono text-[11px] text-ink-3">{d.agents.qc.note}</p>
    </div>,
    <div key="problems" className="flex flex-col gap-3">
      <CardHead name={d.agents.problems.name} agent={d.agents.problems.agent} />
      <RootCauses mock={m} />
      <div className="mt-1 rounded-md border border-border-2 bg-bg-2 p-2.5 text-[11px] text-ink-2">
        <p className="mb-1.5 text-[10px] font-semibold text-ink-3">{m.transcript}</p>
        {m.transcriptLines.slice(0, 2).map((l, i) => (
          <p key={i} className="flex gap-2">
            <span className={`w-14 shrink-0 font-semibold ${i % 2 ? "text-teal" : "text-amber-text"}`}>{l.who}</span>
            <span>{l.text}</span>
          </p>
        ))}
      </div>
    </div>,
    <div key="insights" className="flex flex-col gap-3">
      <CardHead name={d.agents.insights.name} agent={d.agents.insights.agent} />
      <div className="grid grid-cols-3 gap-2">
        {d.agents.insights.items.map((it, i) => (
          <div key={it.label} className="rounded-md border border-border-2 bg-bg-2 p-2.5">
            <p className="text-[10px] text-ink-3">{it.label}</p>
            <p className={`mt-1 text-sm font-bold ${i === 0 ? "text-amber-text" : i === 2 ? "text-ember" : "text-ink"}`}>{it.value}</p>
          </div>
        ))}
      </div>
      <div className="rounded-md border border-border-2 bg-bg-2 p-2.5">
        <p className="mb-1 text-[10px] font-semibold text-ink-3">{m.sentiment}</p>
        <div className="flex h-2 overflow-hidden rounded-full">
          <span className="bg-teal" style={{ width: "54%" }} />
          <span className="bg-amber" style={{ width: "31%" }} />
          <span className="bg-ember" style={{ width: "15%" }} />
        </div>
      </div>
    </div>,
    <div key="teacher" className="flex flex-col gap-3">
      <CardHead name={d.agents.teacher.name} agent={d.agents.teacher.agent} />
      <ol className="flex flex-col gap-2">
        {d.agents.teacher.items.map((it, i) => (
          <li key={it} className="flex items-start gap-2.5 rounded-md border border-border-2 bg-bg-2 p-2.5 text-[11px] text-ink-2">
            <span className="mono inline-grid size-5 shrink-0 place-items-center rounded-full bg-amber text-[10px] font-bold text-on-amber">{localizeDigits(i + 1, locale)}</span>
            {it}
          </li>
        ))}
      </ol>
      <span className="chip !min-h-0 self-start !px-2 !py-0.5 !text-[10px]">
        <Icon name="download" size={10} />
        {m.exportPdf}
      </span>
    </div>,
  ];

  return (
    <section className="py-16 md:py-24" data-stack>
      <div className="container-x grid gap-10 lg:grid-cols-[0.9fr_1.1fr] lg:items-start">
        <div>
          <SectionHeading eyebrow={dict.story.eyebrow} title={dict.story.title} text={dict.story.text} split />
          <ol className="mt-10 flex flex-col divide-y divide-border-2 border-y border-border-2" data-reveal-group>
            {items.map((item, i) => (
              <li key={item.title} className="flex gap-4 py-4" data-stack-item>
                <span className="mono pt-1 text-xs text-amber-text">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
                <div>
                  <h3 className="text-lg font-semibold">{item.title}</h3>
                  <p className="mt-1 text-ink-2">{item.text}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
        <div className="story-cards grid gap-4 sm:grid-cols-2 lg:block" role="img" aria-label={label}>
          {cards.map((card, i) => (
            <div key={i} className="story-card panel panel-glow p-4" data-stack-card aria-hidden="true">
              {card}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function CardHead({ name, agent }: { name: string; agent: string }) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-border-2 pb-2">
      <span className="flex items-baseline gap-2">
        <span className="text-[12px] font-semibold">{name}</span>
        <span className="mono text-[10px] text-ink-3">{agent}</span>
      </span>
      <span className="chip chip-teal !min-h-0 !px-1.5 !py-0 !text-[10px]">
        <span className="dot" aria-hidden="true" />
      </span>
    </div>
  );
}
