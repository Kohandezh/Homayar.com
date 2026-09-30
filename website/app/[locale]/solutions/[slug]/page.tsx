import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { bidi } from "@/lib/bidi";
import { isLocale, locales, localizeDigits } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { solutionSlugs, type SolutionSlug } from "@/lib/site";
import { PageHero, Section } from "@/components/ui/Section";
import { CheckList, Chip } from "@/components/ui/Cards";
import { CommandCta } from "@/components/site/CommandCta";
import { Icon } from "@/components/ui/Icon";
import { DayTimeline } from "@/components/story/DayTimeline";

export const dynamicParams = false;

export function generateStaticParams() {
  return locales.flatMap((locale) => solutionSlugs.map((slug) => ({ locale, slug })));
}

function isSlug(value: string): value is SolutionSlug {
  return (solutionSlugs as readonly string[]).includes(value);
}

export async function generateMetadata({ params }: PageProps<"/[locale]/solutions/[slug]">): Promise<Metadata> {
  const { locale, slug } = await params;
  if (!isLocale(locale) || !isSlug(slug)) return {};
  const dict = await getDictionary(locale);
  const role = dict.solutions.roles[slug];
  return pageMetadata({ locale, path: `/solutions/${slug}`, title: role.meta.title, description: role.meta.description, siteName: dict.brand });
}

export default async function SolutionPage({ params }: PageProps<"/[locale]/solutions/[slug]">) {
  const { locale, slug } = await params;
  if (!isLocale(locale) || !isSlug(slug)) notFound();
  const dict = await getDictionary(locale);
  const role = dict.solutions.roles[slug];
  const t = (text: string) => bidi(text, locale);

  return (
    <>
      <PageHero
        eyebrow={role.name}
        title={role.hero.title}
        text={t(role.hero.text)}
        back={
          <Link href={`/${locale}/solutions`} className="link-arrow mb-4 text-sm" data-reveal>
            <Icon name={locale === "fa" ? "arrowRight" : "arrowLeft"} size={16} />
            {dict.nav.solutions}
          </Link>
        }
      >
        <Link href={`/${locale}/contact`} className="btn btn-primary" data-magnetic>
          {dict.solutions.cta}
        </Link>
      </PageHero>

      <DayTimeline dict={dict} locale={locale} slug={slug} />

      <Section tone="raised">
        <div className="grid gap-6 lg:grid-cols-[1fr_1.3fr]">
          <div className="panel p-6 md:p-8" data-depth="260">
            <h2 className="text-xl font-bold">{role.needs.title}</h2>
            <ul className="mt-5 flex flex-col gap-4">
              {role.needs.items.map((n, i) => (
                <li key={n} className="flex gap-3">
                  <span className="label inline-grid size-7 shrink-0 place-items-center rounded-full bg-surface-2 text-xs font-bold text-accent-text">{localizeDigits(i + 1, locale)}</span>
                  <span className="text-ink-2">{t(n)}</span>
                </li>
              ))}
            </ul>
          </div>
          <div className="panel panel-glow p-6 md:p-8" data-depth="340">
            <h2 className="text-xl font-bold">{role.gets.title}</h2>
            <div className="mt-5">
              <CheckList items={role.gets.items} render={t} />
            </div>
            <div className="mt-6 border-t border-border-2 pt-5">
              <div className="flex items-center gap-2">
                <h3 className="font-semibold">{role.roadmap.title}</h3>
                <Chip tone="accent">{dict.common.comingSoon}</Chip>
              </div>
              <ul className="mt-3 flex flex-col gap-2 text-ink-2">
                {role.roadmap.items.map((r) => (
                  <li key={r} className="flex items-start gap-2">
                    <span className="mt-2.5 size-1.5 shrink-0 rounded-full bg-accent" />
                    {t(r)}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </Section>

      <CommandCta locale={locale} dict={dict} />
    </>
  );
}
