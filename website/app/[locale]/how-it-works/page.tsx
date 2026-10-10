import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { bidi } from "@/lib/bidi";
import { isLocale, localizeDigits } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero } from "@/components/ui/Section";
import { CommandCta } from "@/components/site/CommandCta";
import { Icon } from "@/components/ui/Icon";
import { SnakePath } from "@/components/story/SnakePath";

export async function generateMetadata({ params }: PageProps<"/[locale]/how-it-works">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return pageMetadata({ locale, path: "/how-it-works", title: dict.howItWorks.meta.title, description: dict.howItWorks.meta.description, siteName: dict.brand });
}

const stepIcons = ["upload", "mic", "users", "sparkles", "layout", "layers", "shield"];
const BARS = Array.from({ length: 22 }, (_, i) => Math.max(0.15, Math.min(1, Math.abs(Math.sin(i * 0.9 + 1.3)) * 0.6 + Math.abs(Math.sin(i * 2.7)) * 0.4)));
const delay = (i: number, step = 140) => ({ ["--sd-d" as string]: `${i * step}ms` });

export default async function HowItWorksPage({ params }: PageProps<"/[locale]/how-it-works">) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);
  const h = dict.howItWorks;
  const sn = h.snake;
  const m = dict.mock;
  const t = (text: string) => bidi(text, locale);
  const lines = dict.demo.transcript[locale].slice(0, 3);
  const speakers = dict.demo.speakers;

  const cards = [
    ...h.steps.map((s) => ({ stage: s.stage, title: s.title, text: s.text })),
    { stage: h.states.eyebrow, title: h.states.title, text: h.states.text },
    { stage: h.policy.eyebrow, title: h.policy.title, text: h.policy.text },
  ];

  const demos = [
    <div key="check" className="sd">
      <div className="sd-bars" aria-hidden="true">
        {BARS.map((b, i) => (
          <span key={i} style={{ ["--h" as string]: b, ...delay(i, 25) }} />
        ))}
      </div>
      <ul className="mt-3 flex flex-wrap gap-2">
        {sn.checks.map((c, i) => (
          <li key={c} className="sd-check flex items-center gap-1.5 text-xs font-semibold text-accent-text" style={delay(i + 2, 180)}>
            <Icon name="check" size={12} strokeWidth={3} className="sd-ok" />
            {c}
          </li>
        ))}
      </ul>
    </div>,
    <div key="whisper" className="sd sd-whisper">
      <div className="sd-bars" aria-hidden="true">
        {BARS.map((b, i) => (
          <span key={i} style={{ ["--h" as string]: b }} />
        ))}
      </div>
      <div className="sd-lines mt-3">
        {lines.map((l, i) => (
          <p key={i} className="sd-line text-sm text-ink-2" style={delay(i, 220)}>
            {l.text}
          </p>
        ))}
      </div>
    </div>,
    <div key="speakers" className="sd sd-lines">
      {lines.map((l, i) => (
        <div key={i} className="flex items-start gap-3 text-sm">
          <span className={`sd-who w-14 shrink-0 text-xs font-bold ${l.who === "customer" ? "sd-who-customer" : "sd-who-agent"}`} style={delay(i, 200)}>
            {speakers[l.who as "customer" | "agent"]}
          </span>
          <span className="text-ink-2">{l.text}</span>
        </div>
      ))}
    </div>,
    <div key="agents" className="sd flex flex-col gap-2">
      {sn.agents.map((a, i) => (
        <div key={a} className="flex items-center gap-3 text-sm">
          <span className="w-20 shrink-0 truncate font-semibold">{a}</span>
          <div className="dot-track flex-1">
            <div className="dot-fill sd-fill" style={{ width: `${[82, 74, 88, 79][i]}%`, ...delay(i, 160) }} />
          </div>
          <span className="label w-6 text-end font-bold text-accent-text">{localizeDigits([82, 74, 88, 79][i], locale)}</span>
        </div>
      ))}
    </div>,
    <div key="console" className="sd grid grid-cols-3 gap-4">
      {sn.tiles.map((tile, i) => (
        <div key={tile} className="sd-tile flex flex-col items-center gap-2 text-center text-xs font-semibold" style={delay(i, 160)}>
          <Icon name={["layout", "file", "bot"][i]} size={22} className="text-accent-text" />
          {tile}
        </div>
      ))}
    </div>,
    <div key="stages" className="sd">
      <ol className="flex flex-col divide-y divide-border-2">
        {m.stages.map((s, i) => (
          <li key={s} className="flex items-center justify-between gap-2 py-2 text-sm">
            <span className="flex items-center gap-3">
              <span className="label w-4 text-xs font-bold text-accent-text">{localizeDigits(i + 1, locale)}</span>
              {s}
            </span>
            <span className="sd-state text-xs font-semibold" style={delay(i, 200)}>
              <span className="sd-state-wait text-ink-3" aria-hidden="true">{h.rail.todo}</span>
              <span className={`sd-state-final flex items-center gap-1.5 ${i === 2 ? "text-ink" : "text-accent-text"}`}>
                <span className={`glow-dot ${i === 2 ? "glow-dot-strong" : ""}`} aria-hidden="true" />
                {m.stageStates[i]}
              </span>
            </span>
          </li>
        ))}
      </ol>
      <dl className="mt-4 grid gap-1.5 text-xs sm:grid-cols-2">
        {h.states.items.map((it) => (
          <div key={it.label} className="flex gap-1.5">
            <dt className="shrink-0 font-semibold text-accent-text">{it.label}:</dt>
            <dd className="text-ink-2">{t(it.text)}</dd>
          </div>
        ))}
      </dl>
    </div>,
    <div key="policy" className="sd">
      <ul className="flex flex-col divide-y divide-border-2">
        {sn.providers.map((p, i) => (
          <li key={p.name} className="flex items-center justify-between gap-2 py-2 text-sm">
            <span>{t(p.name)}</span>
            <span className={`sd-verdict flex items-center gap-1.5 text-xs font-bold ${p.allowed ? "text-accent-text" : "text-ink-3"}`} style={delay(i, 220)}>
              <Icon name={p.allowed ? "check" : "x"} size={12} strokeWidth={3} />
              {p.allowed ? sn.allowed : sn.refused}
            </span>
          </li>
        ))}
      </ul>
      <Link href={`/${locale}/security#routing`} className="link-arrow mt-3 !min-h-0 text-sm">
        {h.policy.link}
        <Icon name={locale === "fa" ? "arrowLeft" : "arrowRight"} size={14} />
      </Link>
    </div>,
  ];

  return (
    // The camera rides the ribbon like a track. Each step is a gate it passes through (design-system/SCENE-RULES.md).
    <div className="relative" data-scene="ride" data-testid="ride">
      <div className="snake relative isolate" data-snake data-testid="snake">
        <SnakePath />
        <div className="relative z-10">
          <div className="naked">
            <PageHero eyebrow={h.hero.eyebrow} title={h.hero.title} text={h.hero.text} align="center">
              <p className="snake-hint label flex items-center justify-center gap-2 text-xs text-ink-3">
                <Icon name="scan" size={13} />
                {sn.hint}
              </p>
            </PageHero>
          </div>
          <ol className="snake-steps container-x naked" aria-label={h.hero.eyebrow}>
            {cards.map((card, i) => (
              <li key={card.title} className="snake-card" data-snake-card data-testid="snake-card">
                <div className="snake-station">
                  <div className="flex items-end gap-4">
                    <span className="outline-num" aria-hidden="true">
                      {localizeDigits(String(i + 1).padStart(2, "0"), locale)}
                    </span>
                    <span className="flex flex-col gap-1 pb-2">
                      <span className="flex items-center gap-2 text-xs font-semibold text-accent-text">
                        <Icon name={stepIcons[i]} size={14} />
                        {card.stage}
                      </span>
                      <span className="snake-here items-center gap-1.5 text-xs font-semibold text-ink">
                        <span className="glow-dot" aria-hidden="true" />
                        {sn.passing}
                      </span>
                    </span>
                  </div>
                  <h2 className="mt-4 text-2xl font-bold md:text-3xl">{t(card.title)}</h2>
                  <p className="mt-3 text-ink-2">{t(card.text)}</p>
                  <div className="mt-6" aria-hidden={i < 5 ? "true" : undefined}>
                    {demos[i]}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </div>
      <CommandCta locale={locale} dict={dict} naked />
    </div>
  );
}
