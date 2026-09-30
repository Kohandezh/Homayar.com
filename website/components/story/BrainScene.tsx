import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { SceneBackdrop } from "@/components/scene/SceneBackdrop";
import { RootCauses, ScoreBars } from "@/components/mockups/parts";
import { Icon } from "@/components/ui/Icon";

const tops = ["16%", "29%", "42%", "55%", "68%"];

export function BrainScene({ dict, locale }: { dict: Dictionary; locale: Locale }) {
  const s = dict.scene.brain;
  const d = dict.demo;
  const m = dict.mock;
  const lines = d.transcript[locale];
  return (
    <section className="relative isolate overflow-hidden" data-scene="brain" data-pin data-brain data-testid="brain-scene">
      <SceneBackdrop kind="brain" />
      <div className="brain-video" aria-hidden="true">
        <video muted playsInline loop preload="none" data-poster="/media/neurons-poster.webp" data-brain-video tabIndex={-1}>
          <source data-src="/media/neurons-720.webm" data-src-wide="/media/neurons-1280.webm" type="video/webm" />
          <source data-src="/media/neurons-720.mp4" data-src-wide="/media/neurons-1280.mp4" type="video/mp4" />
        </video>
      </div>
      <div className="brain-stage container-x py-16 md:py-24">
        <div className="brain-intro mx-auto max-w-2xl text-center" data-brain-intro>
          <p className="eyebrow justify-center">{s.eyebrow}</p>
          <h2 className="display-2 mt-4" data-testid="brain-title">{s.title}</h2>
          <p className="mt-4 text-lg text-ink-2">{s.text}</p>
          <p className="chip mt-5">
            <Icon name="scan" size={13} />
            {s.sample}
          </p>
        </div>

        <div className="brain-core-wrap" aria-hidden="true">
          <div className="brain-core" data-brain-core />
        </div>

        <div className="brain-status mt-10 flex flex-wrap justify-center gap-2">
          <span className="chip chip-accent brain-status-on" data-brain-analyzing>
            <span className="dot dot-live" aria-hidden="true" />
            {s.analyzing}
          </span>
          <span className="chip chip-strong brain-status-done" data-brain-ready>
            <Icon name="check" size={12} strokeWidth={3} />
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
              <span className="label mb-1 block text-[11px] text-ink-3">{d.speakers[l.who as "customer" | "agent"]}</span>
              <p className="panel-glass px-4 py-3 text-base leading-relaxed md:text-lg">{l.text}</p>
            </li>
          ))}
        </ol>

        <div className="brain-report panel panel-glow mx-auto mt-10 max-w-2xl overflow-hidden" data-brain-report data-testid="brain-report">
          <div className="panel-head">
            <span className="window-dots" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
            <span className="text-sm font-semibold">{d.report}</span>
            <span className="chip ms-auto !min-h-0 !px-2 !py-0.5 !text-[10px]">{d.sample}</span>
          </div>
          <div className="grid gap-4 p-4 sm:grid-cols-2">
            <div className="rounded-xl border border-border-2 bg-bg-deep p-3">
              <p className="mb-2 text-[11px] font-semibold text-ink-3">{m.qc}</p>
              <ScoreBars mock={m} locale={locale} limit={4} />
            </div>
            <div className="flex flex-col gap-3">
              <div className="rounded-xl border border-border-2 bg-bg-deep p-3">
                <p className="mb-2 text-[11px] font-semibold text-ink-3">{m.problems}</p>
                <RootCauses mock={m} />
              </div>
              <div className="rounded-xl border border-border-2 bg-bg-deep p-3">
                <p className="mb-1 text-[11px] font-semibold text-ink-3">{d.agents.teacher.name}</p>
                <p className="text-[12px] text-ink-2">{d.agents.teacher.items[1]}</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
