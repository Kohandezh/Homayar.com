import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { bidi } from "@/lib/bidi";
import { isLocale, localizeDigits, type Locale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { Section, SectionHeading } from "@/components/ui/Section";
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

  const pt = h.particles;

  return (
    <>
      <section
        className="particles relative isolate overflow-hidden"
        data-scene="particles"
        data-pin
        data-particles
        data-line1={pt.brand}
        data-line2={pt.slogan}
        data-testid="particle-hero"
      >
        <SceneBackdrop kind="particles" priority />
        <div className="container-x particles-stage">
          <p className="particles-title display" data-particles-title>
            <span className="block">{pt.brand}</span>
            <span className="particles-slogan mt-4 block">{pt.slogan}</span>
          </p>
        </div>
      </section>

      <section className="flight relative isolate overflow-hidden" data-scene="tunnel" data-pin data-flight data-testid="hero-flight">
        <SceneBackdrop kind="tunnel" priority />
        <div className="container-x w-full py-16 md:py-20">
          <div className="mx-auto max-w-4xl text-center" data-flight-content>
            <h1 className="flight-h1">{h.hero.title}</h1>
            <div className="flight-headlines" data-flight-headlines aria-hidden="true">
              {h.hero.headlines.map((line) => (
                <p key={line} className="display flight-headline" data-headline>
                  {line}
                </p>
              ))}
            </div>
          </div>
        </div>
      </section>

      <WarpBand eyebrow={dict.scene.warp.eyebrow} title={dict.scene.warp.title} text={dict.scene.warp.text} />

      <BrainScene dict={dict} locale={locale} />

      <AgentJourney dict={dict} locale={locale} />

      <Section scene="field">
        <div className="naked">
          <SectionHeading eyebrow={dict.story.surfaces.eyebrow} title={dict.story.surfaces.title} text={dict.story.surfaces.text} align="center" split />
        </div>
        {/* The console and the phone hang in the sky on thin cables, like screens in a gallery. */}
        <div className="mt-40 grid items-end gap-x-10 gap-y-40 lg:grid-cols-[1.5fr_1fr]">
          <div className="hang" data-depth="360">
            <AppWindow dict={dict} locale={locale} label={dict.common.decorativeMockup} />
          </div>
          <div className="flex justify-center">
            <div className="hang" data-depth="240">
              <PhoneFrame dict={dict} locale={locale} label={dict.common.decorativeMockup} />
            </div>
          </div>
        </div>
        <ul className="naked mt-20 grid gap-x-10 gap-y-8 sm:grid-cols-2 lg:grid-cols-3" data-reveal-group>
          {h.product.items.map((item, i) => (
            <li key={item.title} className="flex gap-4">
              <span className="label pt-1 text-xs font-semibold text-accent-text">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
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
        <div className="naked mt-10 flex justify-center">
          <Link href={`${base}/product`} className="link-arrow">
            {h.product.link}
            <Icon name={arrow} size={16} />
          </Link>
        </div>
      </Section>

      <Section scene="field">
        <div className="naked">
          <SectionHeading eyebrow={dict.story.flow.eyebrow} title={dict.story.flow.title} text={dict.story.flow.text} align="center" split />
        </div>
        <div className="naked mt-16 grid gap-14 lg:grid-cols-3 lg:gap-10">
          {h.deployment.items.map((item, i) => (
            <article key={item.title} className="flex flex-col items-center gap-5 text-center" data-depth={String(220 + i * 80)}>
              <p className="label text-xs font-semibold text-accent-text">{h.deployment.modes[i]}</p>
              <h3 className={`text-2xl font-bold ${i === 1 ? "text-accent-text" : ""}`}>{item.title}</h3>
              <div className="w-full max-w-xs">
                <FlowDiagram flow={dict.deployment.flow} mode={flowModes[i]} compact naked />
              </div>
              <p className="max-w-sm text-sm text-ink-2">{bidi(item.text, locale)}</p>
            </article>
          ))}
        </div>
        <div className="naked mt-10 flex justify-center">
          <Link href={`${base}/deployment`} className="link-arrow">
            {h.deployment.link}
            <Icon name={arrow} size={16} />
          </Link>
        </div>
      </Section>

      <Section scene="field">
        <div className="naked">
          <SectionHeading eyebrow={dict.story.trust.eyebrow} title={dict.story.trust.title} align="center" split />
        </div>
        <ol className="naked mx-auto mt-12 max-w-3xl divide-y divide-border-2" data-reveal-group>
          {h.trust.items.map((item, i) => (
            <li key={item.title} className="grid gap-2 py-6 sm:grid-cols-[auto_1fr] sm:gap-6">
              <span className="label text-xs font-semibold text-accent-text">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
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
        <div className="naked mt-10 flex justify-center">
          <Link href={`${base}/security`} className="link-arrow">
            {h.trust.link}
            <Icon name={arrow} size={16} />
          </Link>
        </div>
      </Section>

      <CommandCta locale={locale} dict={dict} naked scene="field" />
    </>
  );
}
