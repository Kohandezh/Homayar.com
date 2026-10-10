import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { bidi } from "@/lib/bidi";
import { isLocale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero, Section } from "@/components/ui/Section";
import { CommandCta } from "@/components/site/CommandCta";
import { StarMap } from "@/components/story/StarMap";
import { WorkStepper } from "@/components/story/WorkStepper";

export async function generateMetadata({ params }: PageProps<"/[locale]/about">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return pageMetadata({ locale, path: "/about", title: dict.about.meta.title, description: dict.about.meta.description, siteName: dict.brand });
}

export default async function AboutPage({ params }: PageProps<"/[locale]/about">) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);
  const a = dict.about;
  const t = (text: string) => bidi(text, locale);

  return (
    // The visitor looks up at the night sky. Each principle is a star; scrolling draws the constellation (design-system/SCENE-RULES.md).
    <div className="relative" data-scene="dome" data-testid="dome">
      <div className="naked">
        <PageHero eyebrow={a.hero.eyebrow} title={a.hero.title} text={t(a.hero.text)} align="center" />
      </div>

      <StarMap dict={dict} locale={locale} />

      <WorkStepper dict={dict} locale={locale} />

      <Section>
        <div className="naked grid gap-10 lg:grid-cols-2">
          <div>
            <h2 className="display-2" data-split>{a.who.title}</h2>
            <p className="mt-5 text-lg text-ink-2" data-reveal>{t(a.who.text)}</p>
          </div>
          <div className="border-s-2 border-accent ps-6" data-reveal>
            <p className="flex items-center gap-2 text-sm font-semibold text-accent-text">
              <span className="glow-dot" aria-hidden="true" />
              {dict.common.inProgress}
            </p>
            <h2 className="mt-4 text-2xl font-bold md:text-3xl">{a.status.title}</h2>
            <p className="mt-3 text-ink-2">{t(a.status.text)}</p>
          </div>
        </div>
      </Section>

      <CommandCta locale={locale} dict={dict} naked />
    </div>
  );
}
