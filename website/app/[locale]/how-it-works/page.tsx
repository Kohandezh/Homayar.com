import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { bidi } from "@/lib/bidi";
import { isLocale, localizeDigits } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero } from "@/components/ui/Section";
import { Chip } from "@/components/ui/Cards";
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
          <li key={c} className="chip chip-accent sd-check !text-[11px]" style={delay(i + 2, 180)}>
            <Icon name="check" size={11} strokeWidth={3} className="sd-ok" />
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
          <p key={i} className="sd-line rounded-md bg-bg-2 px-2 py-1 text-[11px] text-ink-2" style={delay(i, 220)}>
            {l.text}
          </p>
        ))}
      </div>
    </div>,
    <div key="speakers" className="sd sd-lines">
      {lines.map((l, i) => (
        <div key={i} className="flex items-start gap-2 text-[11px]">
          <span className={`chip sd-who !min-h-0 shrink-0 !px-2 !py-0 !text-[10px] ${l.who === "customer" ? "sd-who-customer" : "sd-who-agent"}`} style={delay(i, 200)}>
            {speakers[l.who as "customer" | "agent"]}
          </span>
          <span className="text-ink-2">{l.text}</span>
        </div>
      ))}
    </div>,
    <div key="agents" className="sd flex flex-col gap-2">
      {sn.agents.map((a, i) => (
        <div key={a} className="flex items-center gap-2 text-[11px]">
          <span className="w-16 shrink-0 truncate font-semibold">{a}</span>
          <div className="score-track flex-1">
            <div className="sd-fill" style={{ width: `${[82, 74, 88, 79][i]}%`, ...delay(i, 160) }} />
          </div>
          <span className="label w-6 text-end font-semibold">{localizeDigits([82, 74, 88, 79][i], locale)}</span>
        </div>
      ))}
    </div>,
    <div key="console" className="sd grid grid-cols-3 gap-2">
      {sn.tiles.map((tile, i) => (
        <div key={tile} className="sd-tile flex flex-col items-center gap-1.5 rounded-md border border-border-2 bg-bg-2 p-2.5 text-center text-[10px] font-semibold" style={delay(i, 160)}>
          <Icon name={["layout", "file", "bot"][i]} size={16} className="text-accent-text" />
          {tile}
        </div>
      ))}
    </div>,
    <div key="stages" className="sd">
      <ol className="flex flex-col gap-1.5">
        {m.stages.map((s, i) => (
          <li key={s} className="flex items-center justify-between gap-2 rounded-md border border-border-2 bg-bg-2 px-2.5 py-1.5 text-[11px]">
            <span className="flex items-center gap-2">
              <span className="label inline-grid size-4 place-items-center rounded-full bg-surface-2 text-[9px] font-bold">{localizeDigits(i + 1, locale)}</span>
              {s}
            </span>
            <span className="sd-state" style={delay(i, 200)}>
              <span className="sd-state-wait chip !min-h-0 !px-2 !py-0.5 !text-[10px]">{h.rail.todo}</span>
              <span className={`sd-state-final chip !min-h-0 !px-2 !py-0.5 !text-[10px] ${i === 2 ? "chip-strong" : "chip-accent"}`}>{m.stageStates[i]}</span>
            </span>
          </li>
        ))}
      </ol>
      <dl className="mt-3 grid gap-1 text-[11px] sm:grid-cols-2">
        {h.states.items.map((it) => (
          <div key={it.label} className="flex gap-1.5">
            <dt className="shrink-0 font-semibold text-accent-text">{it.label}:</dt>
            <dd className="text-ink-2">{t(it.text)}</dd>
          </div>
        ))}
      </dl>
    </div>,
    <div key="policy" className="sd">
      <ul className="flex flex-col gap-1.5">
        {sn.providers.map((p, i) => (
          <li key={p.name} className="flex items-center justify-between gap-2 rounded-md border border-border-2 bg-bg-2 px-2.5 py-1.5 text-[11px]">
            <span>{t(p.name)}</span>
            <span className={`sd-verdict chip !min-h-0 !px-2 !py-0 !text-[10px] ${p.allowed ? "chip-accent" : "chip-strong"}`} style={delay(i, 220)}>
              <Icon name={p.allowed ? "check" : "x"} size={10} strokeWidth={3} />
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
    <>
      <div className="snake relative isolate" data-snake data-testid="snake">
        <SnakePath />
        <div className="relative z-10">
          <PageHero eyebrow={h.hero.eyebrow} title={h.hero.title} text={h.hero.text}>
            <p className="chip snake-hint">
              <Icon name="scan" size={13} />
              {sn.hint}
            </p>
          </PageHero>
          <ol className="snake-steps container-x" aria-label={h.hero.eyebrow}>
            {cards.map((card, i) => (
              <li key={card.title} className="snake-card" data-snake-card data-testid="snake-card">
                <div className="panel p-5 md:p-6">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="inline-grid size-8 place-items-center rounded-full bg-surface-2 text-accent-text">
                      <Icon name={stepIcons[i]} size={15} />
                    </span>
                    <span className="label text-xs text-ink-3">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
                    <Chip>{card.stage}</Chip>
                    <span className="snake-here chip chip-accent ms-auto !text-[11px]">
                      <span className="dot dot-live" aria-hidden="true" />
                      {sn.passing}
                    </span>
                  </div>
                  <h2 className="mt-4 text-xl font-semibold md:text-2xl">{t(card.title)}</h2>
                  <p className="mt-2 text-ink-2">{t(card.text)}</p>
                  <div className="mt-4 rounded-xl border border-border-2 bg-bg-deep p-3" aria-hidden={i < 5 ? "true" : undefined}>
                    {demos[i]}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </div>
      <CommandCta locale={locale} dict={dict} />
    </>
  );
}
