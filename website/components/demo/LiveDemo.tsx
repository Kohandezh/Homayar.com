"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { Dictionary } from "@/i18n/en";
import { localizeDigits, type Locale } from "@/lib/i18n";
import { Icon } from "@/components/ui/Icon";

type Demo = Dictionary["demo"];
type Mock = Dictionary["mock"];
type TranscriptLang = "fa" | "en";

const BAR_HEIGHTS = Array.from({ length: 56 }, (_, i) => {
  const a = Math.abs(Math.sin(i * 0.9 + 1.3)) * 0.55 + Math.abs(Math.sin(i * 2.7)) * 0.35;
  return Math.max(0.12, Math.min(1, a + 0.1));
});

const SCORE_KEYS = [0, 1, 2, 3, 4];

export function LiveDemo({
  demo,
  mock,
  locale,
  brand,
  frame = "stage",
}: {
  demo: Demo;
  mock: Mock;
  locale: Locale;
  brand: string;
  frame?: "stage" | "window";
}) {
  const [lang, setLang] = useState<TranscriptLang>(locale);
  const [playing, setPlaying] = useState(false);
  const [finished, setFinished] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const controls = useRef<{ play: () => void; pause: () => void; restart: () => void } | null>(null);
  const userPaused = useRef(false);
  const uid = useId();
  const rtlTranscript = lang === "fa";
  const lines = demo.transcript[lang];
  const scores = SCORE_KEYS.map((k) => mock.scores[k]);

  useEffect(() => {
    const root = rootRef.current;
    if (!root || !document.documentElement.classList.contains("motion")) return;
    let cancelled = false;
    let cleanup: (() => void) | undefined;

    import("@/lib/gsap").then(({ gsap, ScrollTrigger, MOTION_OK }) => {
      if (cancelled) return;
      const mm = gsap.matchMedia();
      mm.add(MOTION_OK, () => {
        const q = (sel: string) => Array.from(root.querySelectorAll<HTMLElement>(sel));
        const bars = q(".wave-bar");
        const head = root.querySelector<HTMLElement>(".wave-head");
        const lineEls = q(".demo-line");
        const textEls = q(".demo-text");
        const fills = q(".demo-fill");
        const nums = q(".demo-num");
        const findings = {
          problems: q('[data-agent="problems"] .demo-finding'),
          insights: q('[data-agent="insights"] .demo-finding'),
          teacher: q('[data-agent="teacher"] .demo-finding'),
        };
        const agentStates = q(".demo-agent-state");
        const stageStates = q(".demo-stage-state");
        const ready = root.querySelector<HTMLElement>(".demo-ready");
        const texts = textEls.map((el) => el.dataset.text ?? "");

        const setStage = (i: number, state: "waiting" | "running" | "done") => {
          const el = stageStates[i];
          if (!el) return;
          el.textContent = demo.states[state];
          el.className = `demo-stage-state chip !min-h-0 !px-2 !py-0.5 !text-[10px] ${state === "done" ? "chip-accent" : state === "running" ? "chip-accent" : ""}`;
        };
        const resetStages = () => {
          for (let i = 0; i < stageStates.length; i++) setStage(i, "waiting");
        };
        const reset = () => {
          resetStages();
          textEls.forEach((el) => (el.textContent = ""));
          nums.forEach((el) => (el.textContent = localizeDigits(0, locale)));
          gsap.set(bars, { scaleY: 0.08 });
          gsap.set(head, { scaleX: 0 });
          gsap.set(lineEls, { opacity: 0, y: 6 });
          gsap.set(fills, { scaleX: 0 });
          gsap.set([...findings.problems, ...findings.insights, ...findings.teacher], { opacity: 0, y: 10 });
          gsap.set(agentStates, { opacity: 0 });
          gsap.set(ready, { opacity: 0, y: 4 });
        };
        reset();

        const tl = gsap.timeline({
          paused: true,
          defaults: { ease: "power2.out" },
          onStart: () => setPlaying(true),
          onComplete: () => {
            setPlaying(false);
            setFinished(true);
          },
        });
        tl.call(reset, [], 0);
        tl.call(() => setStage(0, "running"), [], 0.05);
        tl.to(bars, { scaleY: (i: number) => BAR_HEIGHTS[i], duration: 0.7, stagger: { each: 0.01 } }, 0.1);
        tl.call(() => {
          setStage(0, "done");
          setStage(1, "running");
        }, [], 0.95);

        const transcriptDur = 7.5;
        tl.addLabel("transcript", 1.1);
        tl.to(head, { scaleX: 1, duration: transcriptDur, ease: "none" }, "transcript");
        lineEls.forEach((line, i) => {
          const at = 1.1 + (i * transcriptDur) / lineEls.length;
          const text = texts[i];
          const state = { n: 0 };
          tl.to(line, { opacity: 1, y: 0, duration: 0.35 }, at);
          tl.to(
            state,
            {
              n: text.length,
              duration: Math.min(1.2, Math.max(0.5, text.length * 0.02)),
              ease: "none",
              onUpdate: () => {
                textEls[i].textContent = text.slice(0, Math.round(state.n));
              },
            },
            at + 0.1,
          );
        });
        tl.call(() => {
          setStage(1, "done");
          setStage(2, "running");
        }, [], 1.1 + transcriptDur);
        tl.call(() => {
          setStage(2, "done");
          setStage(3, "running");
        }, [], 1.1 + transcriptDur + 0.7);

        const agentsAt = 1.1 + transcriptDur + 0.9;
        tl.addLabel("agents", agentsAt);
        tl.to(agentStates, { opacity: 1, duration: 0.3, stagger: 0.15 }, "agents");
        tl.to(fills, { scaleX: 1, duration: 1.3, stagger: 0.1, ease: "power3.out" }, "agents+=0.2");
        const count = { v: 0 };
        tl.to(
          count,
          {
            v: 1,
            duration: 1.5,
            ease: "power2.out",
            onUpdate: () => {
              nums.forEach((el) => {
                const target = Number(el.dataset.value ?? 0);
                el.textContent = localizeDigits(Math.round(target * count.v), locale);
              });
            },
          },
          "agents+=0.2",
        );
        tl.to(findings.problems, { opacity: 1, y: 0, duration: 0.5, stagger: 0.25 }, "agents+=0.8");
        tl.to(findings.insights, { opacity: 1, y: 0, duration: 0.5, stagger: 0.2 }, "agents+=1.5");
        tl.to(findings.teacher, { opacity: 1, y: 0, duration: 0.5, stagger: 0.25 }, "agents+=2.2");
        tl.call(() => setStage(3, "done"), [], "agents+=3.1");
        tl.to(ready, { opacity: 1, y: 0, duration: 0.4 }, "agents+=3.1");

        let inView = false;
        const autoplay = () => {
          if (inView && !userPaused.current && !document.hidden && tl.progress() < 1) tl.play();
        };
        controls.current = {
          play: () => {
            userPaused.current = false;
            if (tl.progress() >= 1) tl.restart();
            else tl.play();
            setPlaying(true);
          },
          pause: () => {
            userPaused.current = true;
            tl.pause();
            setPlaying(false);
          },
          restart: () => {
            userPaused.current = false;
            setFinished(false);
            tl.restart();
            setPlaying(true);
          },
        };
        const st = ScrollTrigger.create({
          trigger: root,
          start: "top 78%",
          end: "bottom 12%",
          onEnter: () => {
            inView = true;
            autoplay();
          },
          onEnterBack: () => {
            inView = true;
            autoplay();
          },
          onLeave: () => {
            inView = false;
            if (tl.isActive()) tl.pause();
          },
          onLeaveBack: () => {
            inView = false;
            if (tl.isActive()) tl.pause();
          },
        });
        const onVisibility = () => {
          if (document.hidden) tl.pause();
          else autoplay();
        };
        document.addEventListener("visibilitychange", onVisibility);
        return () => {
          document.removeEventListener("visibilitychange", onVisibility);
          st.kill();
          tl.kill();
          controls.current = null;
        };
      });
      cleanup = () => mm.revert();
    });

    return () => {
      cancelled = true;
      cleanup?.();
    };
  }, [lang, locale, demo]);

  const toggle = () => {
    if (!controls.current) return;
    if (playing) controls.current.pause();
    else controls.current.play();
  };

  const body = (
    <div className="grid gap-4 p-4 lg:grid-cols-[1.05fr_1fr] lg:gap-5 lg:p-5">
      <div className="flex flex-col gap-4">
        <div className="rounded-xl border border-border-2 bg-bg-2 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="label text-[11px] text-ink-3" dir="ltr">
              {demo.recording}
            </span>
            <span className="label text-[11px] text-ink-2">{demo.call}</span>
          </div>
          <div className="relative mt-3 overflow-hidden rounded-lg">
            <div className="wave" aria-hidden="true">
              {BAR_HEIGHTS.map((h, i) => (
                <span key={i} className="wave-bar" style={{ height: `${Math.round(h * 100)}%` }} />
              ))}
            </div>
            <span className="wave-head pointer-events-none absolute inset-0 bg-accent/15" style={{ transformOrigin: locale === "fa" ? "100% 50%" : "0 50%" }} aria-hidden="true" />
          </div>
          <ol className="mt-3 grid gap-1.5 sm:grid-cols-2">
            {mock.stages.map((s, i) => (
              <li key={s} className="flex items-center justify-between gap-2 rounded-md border border-border-2 bg-surface px-2.5 py-1.5 text-[11px]">
                <span className="flex items-center gap-2">
                  <span className="label inline-grid size-4 place-items-center rounded-full bg-surface-2 text-[9px] font-bold">{localizeDigits(i + 1, locale)}</span>
                  {s}
                </span>
                <span className="demo-stage-state chip chip-accent !min-h-0 !px-2 !py-0.5 !text-[10px]">{demo.states.done}</span>
              </li>
            ))}
          </ol>
        </div>

        <div className="flex-1 rounded-xl border border-border-2 bg-bg-2 p-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-[11px] font-semibold text-ink-3">{mock.transcript}</p>
            <div className="demo-controls items-center gap-1" role="group" aria-label={demo.transcriptLang}>
              {(["fa", "en"] as TranscriptLang[]).map((l) => (
                <button
                  key={l}
                  type="button"
                  lang={l}
                  aria-pressed={lang === l}
                  onClick={() => setLang(l)}
                  className={`label min-h-7 rounded-full px-2.5 text-[11px] font-semibold ${lang === l ? "bg-accent/15 text-accent-text" : "text-ink-3 hover:text-ink"}`}
                >
                  {l === "fa" ? "فارسی" : "English"}
                </button>
              ))}
            </div>
          </div>
          <ol className="flex flex-col gap-2 text-[12px]" dir={rtlTranscript ? "rtl" : "ltr"} lang={lang}>
            {lines.map((l, i) => (
              <li key={`${lang}-${i}`} className="demo-line flex gap-2">
                <span className={`label w-16 shrink-0 text-[10px] font-semibold ${l.who === "agent" ? "text-accent-text" : "text-accent-text"}`} lang={locale}>
                  {demo.speakers[l.who as "customer" | "agent"]}
                </span>
                <span className="demo-text min-h-[1.2em] text-ink-2" data-text={l.text}>
                  {l.text}
                </span>
              </li>
            ))}
          </ol>
        </div>
      </div>

      <div className="flex flex-col gap-3 rounded-xl border border-border-2 bg-bg-2 p-3">
        <div className="flex items-center justify-between">
          <span className="text-[12px] font-semibold">{demo.report}</span>
          <span className="demo-ready chip chip-accent !min-h-0 !px-2 !py-0.5 !text-[10px]">
            <Icon name="check" size={10} strokeWidth={3} />
            {demo.ready}
          </span>
        </div>

        <div className="rounded-lg border border-border-2 bg-surface p-3" data-agent="qc">
          <AgentHead name={demo.agents.qc.name} agent={demo.agents.qc.agent} done={demo.states.done} />
          <div className="mt-2 flex flex-col gap-2">
            {scores.map((s, i) => (
              <div key={s.label} className="flex items-center gap-2 text-[11px]">
                <span className={`w-24 shrink-0 truncate ${i === 0 ? "font-semibold text-ink" : "text-ink-2"}`}>{s.label}</span>
                <div className="score-track flex-1">
                  <div className="score-fill demo-fill" style={{ width: `${s.value}%`, background: s.value < 75 ? "#f59e0b" : undefined }} />
                </div>
                <span className="demo-num label w-6 text-end font-semibold" data-value={s.value}>
                  {localizeDigits(s.value, locale)}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
          <div className="rounded-lg border border-border-2 bg-surface p-3" data-agent="problems">
            <AgentHead name={demo.agents.problems.name} agent={demo.agents.problems.agent} done={demo.states.done} />
            <ul className="mt-2 flex flex-col gap-1.5">
              {demo.agents.problems.items.map((it, i) => (
                <li key={it.label} className="demo-finding flex items-center justify-between gap-2 rounded-md bg-surface-2 px-2 py-1.5 text-[11px]">
                  <span className="truncate">{it.label}</span>
                  <span className={`chip shrink-0 !min-h-0 !px-1.5 !py-0 !text-[10px] ${i === 0 ? "chip-strong" : "chip-accent"}`}>{it.severity}</span>
                </li>
              ))}
            </ul>
          </div>
          <div className="rounded-lg border border-border-2 bg-surface p-3" data-agent="insights">
            <AgentHead name={demo.agents.insights.name} agent={demo.agents.insights.agent} done={demo.states.done} />
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {demo.agents.insights.items.map((it) => (
                <li key={it.label} className="demo-finding chip !min-h-0 !py-1 !text-[10px]">
                  <span className="text-ink-3">{it.label}</span>
                  <span className="font-semibold text-ink">{it.value}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="rounded-lg border border-border-2 bg-surface p-3" data-agent="teacher">
          <AgentHead name={demo.agents.teacher.name} agent={demo.agents.teacher.agent} done={demo.states.done} />
          <ul className="mt-2 flex flex-col gap-1.5 text-[11px] text-ink-2">
            {demo.agents.teacher.items.map((it) => (
              <li key={it} className="demo-finding flex items-start gap-2">
                <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-accent" aria-hidden="true" />
                {it}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );

  const controlsRow = (
    <div className="demo-controls items-center gap-1.5">
      <button type="button" onClick={toggle} className="inline-flex min-h-9 items-center gap-1.5 rounded-full border border-border-2 bg-surface px-3 text-xs font-semibold hover:bg-surface-2" aria-label={playing ? demo.pause : demo.play}>
        <Icon name={playing ? "pause" : "play"} size={14} />
        <span className="hidden sm:inline">{playing ? demo.pause : demo.play}</span>
      </button>
      <button type="button" onClick={() => controls.current?.restart()} className="inline-grid size-9 place-items-center rounded-full border border-border-2 bg-surface hover:bg-surface-2" aria-label={demo.replay} disabled={!finished && !playing}>
        <Icon name="rotate" size={14} />
      </button>
    </div>
  );

  return (
    <div ref={rootRef} className="panel panel-glow overflow-hidden" role="region" aria-labelledby={`${uid}-title`} data-testid="live-demo">
      {frame === "window" ? (
        <div className="panel-head">
          <span className="window-dots" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
          <span className="label ms-3 hidden rounded-md bg-surface px-2 py-0.5 text-[10px] text-ink-3 sm:inline" dir="ltr">
            app.homayar.local/calls/104
          </span>
          <span id={`${uid}-title`} className="chip chip-accent ms-auto">
            <span className="dot dot-live" aria-hidden="true" />
            {demo.label}
          </span>
          <span className="chip hidden sm:inline-flex">{demo.sample}</span>
          {controlsRow}
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2 border-b border-border-2 bg-bg-2 px-4 py-3">
          <span id={`${uid}-title`} className="chip chip-accent">
            <span className="dot dot-live" aria-hidden="true" />
            {demo.label}
          </span>
          <span className="chip">{demo.sample}</span>
          <span className="label hidden text-[11px] text-ink-3 md:inline">{brand}</span>
          <span className="ms-auto">{controlsRow}</span>
        </div>
      )}
      {body}
      <p className="border-t border-border-2 px-4 py-2 text-[11px] text-ink-3">{demo.sampleNote}</p>
    </div>
  );
}

function AgentHead({ name, agent, done }: { name: string; agent: string; done: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="flex items-baseline gap-2">
        <span className="text-[11px] font-semibold">{name}</span>
        <span className="label text-[10px] text-ink-3">{agent}</span>
      </span>
      <span className="demo-agent-state chip chip-accent !min-h-0 !px-1.5 !py-0 !text-[10px]">{done}</span>
    </div>
  );
}
