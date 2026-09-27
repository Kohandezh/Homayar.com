import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { bidi } from "@/lib/bidi";
import { isLocale, localizeDigits, type Locale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { Section, SectionHeading } from "@/components/ui/Section";
import { Chip } from "@/components/ui/Cards";
import { Icon } from "@/components/ui/Icon";
import { AppWindow } from "@/components/mockups/AppWindow";
import { PhoneFrame } from "@/components/mockups/PhoneFrame";
import { HeroStage } from "@/components/scene/HeroStage";
import { LiveDemo } from "@/components/demo/LiveDemo";
import { AgentStory } from "@/components/story/AgentStory";
import { FlowDiagram } from "@/components/story/FlowDiagram";
import { CommandCta } from "@/components/site/CommandCta";

export async function generateMetadata({ params }: PageProps<"/[locale]">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return {
    ...pageMetadata({ locale, path: "", title: dict.home.meta.title, description: dict.home.meta.description, siteName: dict.brand }),
    title: { absolute: dict.home.meta.title },
  };
}

const flowModes = ["saas", "self-hosted", "hybrid"] as const;

export default async function HomePage({ params }: PageProps<"/[locale]">) {
  const { locale: raw } = await params;
  if (!isLocale(raw)) notFound();
  const locale: Locale = raw;
  const dict = await getDictionary(locale);
  const h = dict.home;
  const base = `/${locale}`;
  const arrow = locale === "fa" ? "arrowLeft" : "arrowRight";

  return (
    <>
      <HeroStage label={dict.common.decorativeMockup} rtl={locale === "fa"} className="min-h-[calc(100svh-var(--header-h))]">
        <div className="container-x flex min-h-[calc(100svh-var(--header-h))] flex-col justify-between py-12 md:py-16">
          <div className="max-w-4xl pt-8 md:pt-16">
            <p className="chip chip-amber" data-reveal>
              <span className="dot dot-live" aria-hidden="true" />
              {h.hero.eyebrow}
            </p>
            <h1 className="display mt-6 max-w-[16ch]" data-split>
              {h.hero.title}
            </h1>
            <p className="mt-6 max-w-2xl text-lg text-ink-2 md:text-xl" data-reveal>
              {h.hero.text}
            </p>
            <div className="mt-8 flex flex-wrap gap-3" data-reveal>
              <Link href={`${base}/contact`} className="btn btn-primary" data-magnetic>
                {h.hero.primary}
                <Icon name={arrow} size={18} />
              </Link>
              <Link href={`${base}/product`} className="btn btn-ghost">
                {h.hero.secondary}
              </Link>
            </div>
          </div>
          <div className="mt-16 flex flex-wrap items-end justify-between gap-4">
            <ul className="flex flex-wrap gap-2" data-reveal-group>
              {h.hero.facts.map((f) => (
                <li key={f}>
                  <Chip>{bidi(f, locale)}</Chip>
                </li>
              ))}
            </ul>
            <p className="mono flex items-center gap-2 text-xs text-ink-3" data-reveal>
              <Icon name="scan" size={14} />
              {h.hero.hint}
            </p>
          </div>
        </div>
      </HeroStage>

      <Section className="!pt-4">
        <LiveDemo demo={dict.demo} mock={dict.mock} locale={locale} brand={dict.brand} frame="stage" />
      </Section>

      <AgentStory dict={dict} locale={locale} />

      <Section tone="raised">
        <SectionHeading eyebrow={dict.story.surfaces.eyebrow} title={dict.story.surfaces.title} text={dict.story.surfaces.text} split />
        <div className="mt-12 grid gap-4 lg:grid-cols-[1.5fr_1fr]">
          <div data-reveal>
            <AppWindow dict={dict} locale={locale} label={dict.common.decorativeMockup} />
          </div>
          <div className="flex flex-col gap-4">
            <div className="flex justify-center" data-reveal="0.1">
              <div data-parallax="-6">
                <PhoneFrame dict={dict} locale={locale} label={dict.common.decorativeMockup} />
              </div>
            </div>
          </div>
        </div>
        <ul className="mt-6 grid gap-px overflow-hidden rounded-2xl border border-border-2 bg-border-2 sm:grid-cols-2 lg:grid-cols-3" data-reveal-group>
          {h.product.items.map((item, i) => (
            <li key={item.title} className="flex gap-4 bg-surface p-5">
              <span className="mono pt-1 text-xs text-amber-text">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
              <div>
                <h3 className="flex items-center gap-2 font-semibold">
                  <Icon name={item.icon} size={16} className="text-teal" />
                  {item.title}
                </h3>
                <p className="mt-1 text-sm text-ink-2">{bidi(item.text, locale)}</p>
              </div>
            </li>
          ))}
        </ul>
        <div className="mt-6">
          <Link href={`${base}/product`} className="link-arrow">
            {h.product.link}
            <Icon name={arrow} size={16} />
          </Link>
        </div>
      </Section>

      <Section tone="glow">
        <SectionHeading eyebrow={dict.story.flow.eyebrow} title={dict.story.flow.title} text={dict.story.flow.text} split />
        <div className="mt-12 grid gap-4 lg:grid-cols-3" data-reveal-group>
          {h.deployment.items.map((item, i) => (
            <article key={item.title} className={`panel flex flex-col gap-5 p-5 ${i === 1 ? "panel-glow" : ""}`}>
              <div className="flex items-center justify-between">
                <h3 className="text-xl font-bold">{item.title}</h3>
                <Chip tone={i === 1 ? "amber" : "default"}>{flowModes[i]}</Chip>
              </div>
              <div className="rounded-xl border border-border-2 bg-bg-2 p-3">
                <FlowDiagram flow={dict.deployment.flow} mode={flowModes[i]} compact />
              </div>
              <p className="text-sm text-ink-2">{bidi(item.text, locale)}</p>
            </article>
          ))}
        </div>
        <div className="mt-6">
          <Link href={`${base}/deployment`} className="link-arrow">
            {h.deployment.link}
            <Icon name={arrow} size={16} />
          </Link>
        </div>
      </Section>

      <Section>
        <div className="grid gap-10 lg:grid-cols-[0.8fr_1.2fr]">
          <SectionHeading eyebrow={dict.story.trust.eyebrow} title={dict.story.trust.title} split />
          <ol className="divide-y divide-border-2 border-y border-border-2" data-reveal-group>
            {h.trust.items.map((item, i) => (
              <li key={item.title} className="grid gap-2 py-5 sm:grid-cols-[auto_1fr] sm:gap-6">
                <span className="mono text-xs text-amber-text">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
                <div>
                  <h3 className="flex items-center gap-2 text-lg font-semibold">
                    <Icon name={item.icon} size={18} className="text-teal" />
                    {item.title}
                  </h3>
                  <p className="mt-1.5 text-ink-2">{bidi(item.text, locale)}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
        <div className="mt-6 lg:ps-[calc(40%+2.5rem)]">
          <Link href={`${base}/security`} className="link-arrow">
            {h.trust.link}
            <Icon name={arrow} size={16} />
          </Link>
        </div>
      </Section>

      <CommandCta locale={locale} dict={dict} />
    </>
  );
}
