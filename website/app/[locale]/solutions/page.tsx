import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { bidi } from "@/lib/bidi";
import { isLocale, localizeDigits } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { solutionSlugs } from "@/lib/site";
import { PageHero, Section } from "@/components/ui/Section";
import { CommandCta } from "@/components/site/CommandCta";
import { Icon } from "@/components/ui/Icon";
import { KpiGrid, RootCauses, ScoreBars, Stages } from "@/components/mockups/parts";

export async function generateMetadata({ params }: PageProps<"/[locale]/solutions">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return pageMetadata({ locale, path: "/solutions", title: dict.solutions.meta.title, description: dict.solutions.meta.description, siteName: dict.brand });
}

export default async function SolutionsPage({ params }: PageProps<"/[locale]/solutions">) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);
  const s = dict.solutions;
  const m = dict.mock;

  const preview = (slug: string) =>
    slug === "qa-managers" ? (
      <ScoreBars mock={m} locale={locale} limit={4} />
    ) : slug === "supervisors" ? (
      <RootCauses mock={m} />
    ) : slug === "contact-center-operations" ? (
      <KpiGrid mock={m} compact />
    ) : (
      <Stages mock={m} />
    );

  return (
    <>
      <PageHero eyebrow={s.hero.eyebrow} title={s.hero.title} text={s.hero.text} />
      <Section>
        <div className="grid gap-4 md:grid-cols-2" data-reveal-group>
          {solutionSlugs.map((slug, i) => {
            const role = s.roles[slug];
            return (
              <Link key={slug} href={`/${locale}/solutions/${slug}`} className={`panel group flex flex-col gap-5 p-6 transition-colors hover:border-amber/50 md:p-7 ${i === 0 ? "panel-glow" : ""}`}>
                <div className="flex items-center justify-between">
                  <span className="mono text-xs text-amber-text">{localizeDigits(String(i + 1).padStart(2, "0"), locale)}</span>
                  <Icon name={role.icon} size={20} className="text-teal" />
                </div>
                <div className="rounded-xl border border-border-2 bg-bg-2 p-3" aria-hidden="true">
                  {preview(slug)}
                </div>
                <h2 className="text-2xl font-bold">{role.name}</h2>
                <p className="text-ink-2">{bidi(role.short, locale)}</p>
                <span className="link-arrow mt-auto">
                  {dict.common.learnMore}
                  <Icon name={locale === "fa" ? "arrowLeft" : "arrowRight"} size={16} className="transition-transform group-hover:translate-x-1 rtl:group-hover:-translate-x-1" />
                </span>
              </Link>
            );
          })}
        </div>
      </Section>
      <CommandCta locale={locale} dict={dict} />
    </>
  );
}
