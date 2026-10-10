import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { localizeDigits } from "@/lib/i18n";
import { NakedCauses, NakedFigures, NakedScores, NakedSteps } from "@/components/story/Naked";
import { Icon } from "@/components/ui/Icon";
import { SectionHeading } from "@/components/ui/Section";

export function AgentJourney({ dict, locale }: { dict: Dictionary; locale: Locale }) {
  const items = dict.home.outputs.items;
  const d = dict.demo;
  const m = dict.mock;
  const surfaces = [
    <NakedScores key="qc" mock={m} locale={locale} limit={4} />,
    <NakedCauses key="problems" mock={m} />,
    <NakedFigures key="insights" items={d.agents.insights.items} />,
    <NakedSteps key="teacher" items={d.agents.teacher.items} locale={locale} />,
  ];
  const agents = [d.agents.qc, d.agents.problems, d.agents.insights, d.agents.teacher];

  return (
    <section className="journey relative" data-scene="field" data-journey data-testid="agent-journey">
      <div className="container-x naked py-16 md:py-20 lg:py-8">
        <SectionHeading eyebrow={dict.story.eyebrow} title={dict.story.title} text={dict.story.text} align="center" split />
        <div className="journey-progress mx-auto mt-8 h-px w-full max-w-3xl bg-border-2">
          <div className="h-full origin-left bg-accent rtl:origin-right" style={{ transform: "scaleX(0)" }} data-journey-progress />
        </div>
      </div>
      <ol className="journey-track naked px-4 sm:px-6 lg:mt-6 lg:px-8" data-journey-track>
        {items.map((item, i) => (
          <li key={item.title} className="journey-panel flex flex-col gap-5 py-4" data-journey-panel>
            <div className="flex items-end gap-5">
              <span className="outline-num" aria-hidden="true">
                {localizeDigits(String(i + 1).padStart(2, "0"), locale)}
              </span>
              <span className="label pb-2 text-xs font-semibold text-ink-3">{agents[i].agent}</span>
            </div>
            <h3 className="flex items-center gap-2 text-2xl font-bold md:text-3xl">
              <Icon name={item.icon} size={24} className="text-accent-text" />
              {item.title}
            </h3>
            <p className="max-w-xl text-ink-2">{item.text}</p>
            <div className="max-w-xl pt-2" aria-hidden="true">
              {surfaces[i]}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
