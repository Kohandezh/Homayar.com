import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { localizeDigits } from "@/lib/i18n";
import { RootCauses, ScoreBars } from "@/components/mockups/parts";
import { Icon } from "@/components/ui/Icon";
import { SectionHeading } from "@/components/ui/Section";

export function AgentJourney({ dict, locale }: { dict: Dictionary; locale: Locale }) {
  const items = dict.home.outputs.items;
  const d = dict.demo;
  const m = dict.mock;
  const surfaces = [
    <ScoreBars key="qc" mock={m} locale={locale} limit={4} />,
    <RootCauses key="problems" mock={m} />,
    <div key="insights" className="grid grid-cols-3 gap-2">
      {d.agents.insights.items.map((it) => (
        <div key={it.label} className="rounded-md border border-border-2 bg-bg-deep p-2.5">
          <p className="text-[10px] text-ink-3">{it.label}</p>
          <p className="mt-1 text-sm font-bold">{it.value}</p>
        </div>
      ))}
    </div>,
    <ol key="teacher" className="flex flex-col gap-2">
      {d.agents.teacher.items.map((it, i) => (
        <li key={it} className="flex items-start gap-2.5 rounded-md border border-border-2 bg-bg-deep p-2.5 text-[11px] text-ink-2">
          <span className="label inline-grid size-5 shrink-0 place-items-center rounded-full bg-accent text-[10px] text-on-accent">{localizeDigits(i + 1, locale)}</span>
          {it}
        </li>
      ))}
    </ol>,
  ];
  const agents = [d.agents.qc, d.agents.problems, d.agents.insights, d.agents.teacher];

  return (
    <section className="journey relative" data-journey data-testid="agent-journey">
      <div className="container-x py-16 md:py-20 lg:py-8">
        <div className="flex flex-wrap items-end justify-between gap-6">
          <SectionHeading eyebrow={dict.story.eyebrow} title={dict.story.title} text={dict.story.text} split />
          <p className="chip journey-progress-wrap">
            <Icon name="scan" size={13} />
            {dict.scene.journey.hint}
          </p>
        </div>
        <div className="journey-progress mt-6 h-0.5 w-full bg-border-2">
          <div className="h-full origin-left bg-accent rtl:origin-right" style={{ transform: "scaleX(0)" }} data-journey-progress />
        </div>
      </div>
      <ol className="journey-track px-4 sm:px-6 lg:mt-4 lg:px-8" data-journey-track>
        {items.map((item, i) => (
          <li key={item.title} className="journey-panel panel flex flex-col gap-4 p-5 md:p-6" data-journey-panel>
            <div className="flex items-center justify-between">
              <span className="chip chip-accent">
                {dict.scene.journey.step} {localizeDigits(String(i + 1).padStart(2, "0"), locale)}
              </span>
              <span className="label text-[11px] text-ink-3">{agents[i].agent}</span>
            </div>
            <h3 className="flex items-center gap-2 text-2xl font-bold">
              <Icon name={item.icon} size={22} className="text-accent-text" />
              {item.title}
            </h3>
            <p className="text-ink-2">{item.text}</p>
            <div className="rounded-xl border border-border-2 bg-bg-deep p-3" aria-hidden="true">
              {surfaces[i]}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
