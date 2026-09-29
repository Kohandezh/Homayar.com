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

      <Section scene="field">
        <h2 className="display-2" data-split>{a.principles.title}</h2>
        <ol className="mt-10 grid gap-px overflow-hidden rounded-2xl border border-border-2 bg-border-2 sm:grid-cols-2 lg:grid-cols-3" data-reveal-group>
          {a.principles.items.map((p, i) => (
            <li key={p.title} className="flex flex-col gap-3 bg-surface p-6">
              <div className="flex items-center justify-between">
                <span className="label text-xs text-accent-text">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
                <Icon name={p.icon} size={18} className="text-accent-text" />
              </div>
              <h3 className="text-lg font-semibold">{p.title}</h3>
              <p className="text-sm text-ink-2">{t(p.text)}</p>
            </li>
          ))}
        </ol>
      </Section>

      <Section tone="raised">
        <div className="grid gap-12 lg:grid-cols-2">
          <div>
            <h2 className="display-2" data-split>{a.who.title}</h2>
            <p className="mt-5 text-lg text-ink-2" data-reveal>{t(a.who.text)}</p>
          </div>
          <div>
            <h2 className="display-2" data-split>{a.howWeWork.title}</h2>
            <ol className="relative mt-6 flex flex-col gap-3" data-rail>
              <div aria-hidden="true" className="rail start-[18px]">
                <div className="rail-fill" />
              </div>
              {a.howWeWork.steps.map((s, i) => (
                <li key={s.title} className="relative flex gap-4" data-step>
                  <span className="label relative z-10 inline-grid size-9 shrink-0 place-items-center rounded-full border-4 border-bg-2 bg-surface-2 text-xs font-bold text-accent-text">{localizeDigits(i + 1, locale)}</span>
                  <div className="panel flex-1 p-4" data-reveal>
                    <h3 className="font-semibold">{s.title}</h3>
                    <p className="mt-1 text-sm text-ink-2">{t(s.text)}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </Section>

      <Section tone="glow" scene="field">
        <div className="panel panel-glow mx-auto max-w-3xl p-6 md:p-10" data-depth="260">
          <Chip tone="accent" live>{dict.common.inProgress}</Chip>
          <h2 className="display-2 mt-4">{a.status.title}</h2>
          <p className="mt-4 text-lg text-ink-2">{t(a.status.text)}</p>
        </div>
      </Section>

      <CommandCta locale={locale} dict={dict} />
    </>
  );
}
