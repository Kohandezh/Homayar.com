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
import { SceneBackdrop } from "@/components/scene/SceneBackdrop";
import { WarpBand } from "@/components/story/WarpBand";
import { BrainScene } from "@/components/story/BrainScene";
import { AgentJourney } from "@/components/story/AgentJourney";
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
      <section className="flight relative isolate overflow-hidden" data-scene="tunnel" data-pin data-flight data-testid="hero-flight">
        <SceneBackdrop kind="tunnel" priority />
        <div className="scene-fade-bottom" aria-hidden="true" />
        <div className="container-x w-full py-16 md:py-20">
          <div className="max-w-4xl" data-flight-content>
            <h1 className="display max-w-[16ch]" data-split>
              {h.hero.title}
            </h1>
            <p className="chip chip-accent mt-6" data-flight-item>
              <span className="dot dot-live" aria-hidden="true" />
              {h.hero.eyebrow}
            </p>
            <p className="mt-5 max-w-2xl text-lg text-ink-2 md:text-xl" data-flight-item>
              {h.hero.text}
            </p>
            <ul className="mt-6 flex flex-wrap gap-2" data-flight-item>
              {h.hero.facts.map((f) => (
                <li key={f}>
                  <Chip>{bidi(f, locale)}</Chip>
                </li>
              ))}
            </ul>
            <div className="mt-8 flex flex-wrap gap-3" data-flight-item>
              <Link href={`${base}/contact`} className="btn btn-primary" data-magnetic>
                {h.hero.primary}
                <Icon name={arrow} size={18} />
              </Link>
              <Link href={`${base}/product`} className="btn btn-ghost">
                {h.hero.secondary}
              </Link>
            </div>
            <p className="label mt-10 flex items-center gap-2 text-[11px] text-ink-3" data-flight-item>
              <Icon name="scan" size={14} />
              {dict.scene.flight.hint}
            </p>
          </div>
        </div>
      </section>

      <WarpBand eyebrow={dict.scene.warp.eyebrow} title={dict.scene.warp.title} text={dict.scene.warp.text} />

      <BrainScene dict={dict} locale={locale} />

      <AgentJourney dict={dict} locale={locale} />

      <Section scene="field">
        <SectionHeading eyebrow={dict.story.surfaces.eyebrow} title={dict.story.surfaces.title} text={dict.story.surfaces.text} split />
        <p className="chip mt-4">
          <Icon name="scan" size={13} />
          {dict.scene.depth.hint}
        </p>
        <div className="mt-12 grid gap-4 lg:grid-cols-[1.5fr_1fr]">
          <div data-depth="360">
            <AppWindow dict={dict} locale={locale} label={dict.common.decorativeMockup} />
          </div>
          <div className="flex justify-center">
            <div data-depth="240">
              <PhoneFrame dict={dict} locale={locale} label={dict.common.decorativeMockup} />
            </div>
          </div>
        </div>
        <ul className="mt-6 grid gap-px overflow-hidden rounded-2xl border border-border-2 bg-border-2 sm:grid-cols-2 lg:grid-cols-3" data-reveal-group>
          {h.product.items.map((item, i) => (
            <li key={item.title} className="flex gap-4 bg-surface p-5">
              <span className="label pt-1 text-xs text-accent-text">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
              <div>
                <h3 className="flex items-center gap-2 font-semibold">
                  <Icon name={item.icon} size={16} className="text-accent-text" />
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

      <Section scene="field" tone="glow">
        <SectionHeading eyebrow={dict.story.flow.eyebrow} title={dict.story.flow.title} text={dict.story.flow.text} split />
        <div className="mt-12 grid gap-4 lg:grid-cols-3">
          {h.deployment.items.map((item, i) => (
            <article key={item.title} className={`panel flex flex-col gap-5 p-5 ${i === 1 ? "panel-glow" : ""}`} data-depth={String(220 + i * 80)}>
              <div className="flex items-center justify-between">
                <h3 className="text-xl font-bold">{item.title}</h3>
                <Chip tone={i === 1 ? "accent" : "default"}>{flowModes[i]}</Chip>
              </div>
              <div className="rounded-xl border border-border-2 bg-bg-deep p-3">
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

      <Section scene="field">
        <div className="grid gap-10 lg:grid-cols-[0.8fr_1.2fr]">
          <SectionHeading eyebrow={dict.story.trust.eyebrow} title={dict.story.trust.title} split />
          <ol className="divide-y divide-border-2 border-y border-border-2" data-reveal-group>
            {h.trust.items.map((item, i) => (
              <li key={item.title} className="grid gap-2 py-5 sm:grid-cols-[auto_1fr] sm:gap-6">
                <span className="label text-xs text-accent-text">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
                <div>
                  <h3 className="flex items-center gap-2 text-lg font-semibold">
                    <Icon name={item.icon} size={18} className="text-accent-text" />
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
