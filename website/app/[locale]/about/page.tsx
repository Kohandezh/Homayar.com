import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { bidi } from "@/lib/bidi";
import { isLocale, localizeDigits } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero, Section } from "@/components/ui/Section";
import { Chip } from "@/components/ui/Cards";
import { CommandCta } from "@/components/site/CommandCta";
import { Icon } from "@/components/ui/Icon";
import { ConstellationSvg } from "@/components/story/Constellation";
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
    <>
      <PageHero eyebrow={a.hero.eyebrow} title={a.hero.title} text={t(a.hero.text)} />

      <section className="constellation relative isolate py-16 md:py-24" data-constellation data-testid="constellation">
        <ConstellationSvg />
        <div className="container-x relative z-10">
          <p className="eyebrow">{a.constellation.eyebrow}</p>
          <h2 className="display-2 mt-3">{a.principles.title}</h2>
          <p className="chip mt-4 const-hint">
            <Icon name="scan" size={13} />
            {a.constellation.hint}
          </p>
          <ol className="const-grid">
            {a.principles.items.map((p, i) => (
              <li key={p.title} className="const-card" style={{ ["--k" as string]: i }} data-testid="const-card">
                <span className="const-dot" data-star-dot aria-hidden="true" />
                <div className="panel-glass p-6">
                  <div className="flex items-center justify-between">
                    <span className="label text-xs text-accent-text">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
                    <Icon name={p.icon} size={18} className="text-accent-text" />
                  </div>
                  <h3 className="mt-3 text-lg font-semibold">{p.title}</h3>
                  <p className="mt-2 text-sm text-ink-2">{t(p.text)}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <WorkStepper dict={dict} locale={locale} />

      <Section tone="raised">
        <div className="grid gap-10 lg:grid-cols-2">
          <div>
            <h2 className="display-2" data-split>{a.who.title}</h2>
            <p className="mt-5 text-lg text-ink-2" data-reveal>{t(a.who.text)}</p>
          </div>
          <div className="panel panel-glow p-6 md:p-8" data-reveal>
            <Chip tone="accent" live>{dict.common.inProgress}</Chip>
            <h2 className="mt-4 text-2xl font-bold md:text-3xl">{a.status.title}</h2>
            <p className="mt-3 text-ink-2">{t(a.status.text)}</p>
          </div>
        </div>
      </Section>

      <CommandCta locale={locale} dict={dict} />
    </>
  );
}
