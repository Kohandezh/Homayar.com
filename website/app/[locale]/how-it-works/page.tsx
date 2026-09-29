import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { bidi } from "@/lib/bidi";
import { isLocale, localizeDigits } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero, Section, SectionHeading } from "@/components/ui/Section";
import { Chip } from "@/components/ui/Cards";
import { CommandCta } from "@/components/site/CommandCta";
import { Icon } from "@/components/ui/Icon";

export async function generateMetadata({ params }: PageProps<"/[locale]/how-it-works">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return pageMetadata({ locale, path: "/how-it-works", title: dict.howItWorks.meta.title, description: dict.howItWorks.meta.description, siteName: dict.brand });
}

const stepIcons = ["upload", "mic", "users", "sparkles", "layout"];
const stateTones = ["default", "default", "accent", "accent", "default", "strong"] as const;

export default async function HowItWorksPage({ params }: PageProps<"/[locale]/how-it-works">) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);
  const h = dict.howItWorks;
  const t = (text: string) => bidi(text, locale);

  return (
    <>
      <PageHero
        eyebrow={h.hero.eyebrow}
        title={h.hero.title}
        text={h.hero.text}
        aside={
          <div className="panel p-4" role="img" aria-label={dict.common.decorativeMockup}>
            <div aria-hidden="true" className="flex flex-col gap-2">
              <div className="flex items-center justify-between text-[11px]">
                <span className="label text-ink-3" dir="ltr">{dict.demo.recording}</span>
                <Chip tone="accent" live>{dict.demo.states.running}</Chip>
              </div>
              {dict.mock.stages.map((s, i) => (
                <div key={s} className="flex items-center justify-between gap-2 rounded-md border border-border-2 bg-bg-2 px-2.5 py-2 text-[11px]">
                  <span className="flex items-center gap-2">
                    <span className="label inline-grid size-4 place-items-center rounded-full bg-surface-2 text-[9px] font-bold">{localizeDigits(i + 1, locale)}</span>
                    {s}
                  </span>
                  <span className={`chip !min-h-0 !px-2 !py-0.5 !text-[10px] ${i < 2 ? "chip-accent" : i === 2 ? "" : "chip-accent"}`}>{dict.mock.stageStates[i]}</span>
                </div>
              ))}
            </div>
          </div>
        }
      />

      <Section scene="tunnel">
        <ol className="relative mx-auto max-w-3xl" data-rail>
          <div aria-hidden="true" className="rail">
            <div className="rail-fill" />
          </div>
          {h.steps.map((step, i) => (
            <li key={step.title} className="relative flex gap-5 pb-10 last:pb-0" data-step>
              <span className="relative z-10 inline-grid size-11 shrink-0 place-items-center rounded-full border-4 border-bg bg-surface-2 text-accent-text">
                <Icon name={stepIcons[i]} size={18} />
              </span>
              <div className="panel flex-1 p-5 md:p-6" data-depth="220">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="label text-xs text-ink-3">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
                  <Chip>{step.stage}</Chip>
                  <span className="step-todo chip !text-[11px]">{h.rail.todo}</span>
                  <span className="step-done chip chip-accent !text-[11px]">
                    <Icon name="check" size={11} strokeWidth={3} />
                    {h.rail.done}
                  </span>
                </div>
                <h2 className="mt-4 text-xl font-semibold md:text-2xl">{t(step.title)}</h2>
                <p className="mt-2 text-ink-2">{t(step.text)}</p>
              </div>
            </li>
          ))}
        </ol>
      </Section>

      <Section tone="raised">
        <SectionHeading eyebrow={h.states.eyebrow} title={h.states.title} text={h.states.text} split />
        <dl className="mt-12 grid gap-px overflow-hidden rounded-2xl border border-border-2 bg-border-2 sm:grid-cols-2 lg:grid-cols-3" data-reveal-group>
          {h.states.items.map((item, i) => (
            <div key={item.label} className="bg-surface p-5">
              <dt className="flex items-center gap-2 font-semibold">
                <Chip tone={stateTones[i]} live={i === 2}>
                  {item.label}
                </Chip>
              </dt>
              <dd className="mt-3 text-sm text-ink-2">{t(item.text)}</dd>
            </div>
          ))}
        </dl>
      </Section>

      <Section tone="glow" scene="field">
        <div className="panel panel-glow grid gap-8 p-6 md:grid-cols-[1fr_auto] md:items-center md:p-10" data-reveal>
          <div>
            <p className="eyebrow">{h.policy.eyebrow}</p>
            <h2 className="display-2 mt-4">{h.policy.title}</h2>
            <p className="mt-5 max-w-2xl text-ink-2">{t(h.policy.text)}</p>
          </div>
          <Link href={`/${locale}/security#routing`} className="btn btn-primary" data-magnetic>
            {h.policy.link}
          </Link>
        </div>
      </Section>

      <CommandCta locale={locale} dict={dict} />
    </>
  );
}
