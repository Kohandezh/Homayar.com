import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { SceneBackdrop } from "@/components/scene/SceneBackdrop";
import { NakedCauses, NakedScores } from "@/components/story/Naked";

const tops = ["16%", "29%", "42%", "55%", "68%"];

export function BrainScene({ dict, locale }: { dict: Dictionary; locale: Locale }) {
  const s = dict.scene.brain;
  const d = dict.demo;
  const m = dict.mock;
  const lines = d.transcript[locale];
  return (
    <section className="relative isolate overflow-hidden" data-scene="brain" data-pin data-brain data-testid="brain-scene">
      <SceneBackdrop kind="brain" />
      <div className="brain-stage container-x py-16 md:py-24">
        <div className="brain-intro naked mx-auto max-w-2xl text-center" data-brain-intro>
          <p className="eyebrow justify-center">{s.eyebrow}</p>
          <h2 className="display-2 mt-4" data-testid="brain-title">{s.title}</h2>
          <p className="mt-4 text-lg text-ink-2">{s.text}</p>
          <p className="label mt-4 text-xs text-ink-3">{s.sample}</p>
        </div>

        <div className="brain-status naked mt-10 flex flex-wrap justify-center gap-6 text-sm font-semibold">
          <span className="brain-status-on flex items-center gap-2 text-accent-text" data-brain-analyzing>
            <span className="glow-dot" aria-hidden="true" />
            {s.analyzing}
          </span>
          <span className="brain-status-done flex items-center gap-2 text-ink" data-brain-ready>
            <span className="glow-dot glow-dot-strong" aria-hidden="true" />
            {s.ready}
          </span>
        </div>

        <ol className="brain-lines mt-8" aria-label={s.conversation} data-testid="brain-lines">
          {lines.map((l, i) => (
            <li
              key={i}
              className={`brain-line ${l.who === "customer" ? "brain-line-customer" : "brain-line-agent"}`}
              data-line
              data-who={l.who}
              style={{ "--line-top": tops[i] } as React.CSSProperties}
            >
              <span className="label mb-1 block text-[11px] font-semibold text-accent-text">{d.speakers[l.who as "customer" | "agent"]}</span>
              <p className="naked text-base leading-relaxed text-ink md:text-lg">{l.text}</p>
            </li>
          ))}
        </ol>

        <div className="brain-report naked mx-auto mt-10 max-w-2xl" data-brain-report data-testid="brain-report">
          <div className="flex items-baseline justify-between gap-4">
            <p className="text-xl font-bold">{d.report}</p>
            <p className="label text-xs text-ink-3">{d.sample}</p>
          </div>
          <div className="mt-6 grid gap-8 sm:grid-cols-2">
            <div>
              <p className="mb-3 text-xs font-semibold text-ink-3">{m.qc}</p>
              <NakedScores mock={m} locale={locale} limit={4} />
            </div>
            <div className="flex flex-col gap-6">
              <div>
                <p className="mb-3 text-xs font-semibold text-ink-3">{m.problems}</p>
                <NakedCauses mock={m} />
              </div>
              <div>
                <p className="mb-2 text-xs font-semibold text-ink-3">{d.agents.teacher.name}</p>
                <p className="text-sm text-ink-2">{d.agents.teacher.items[1]}</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
