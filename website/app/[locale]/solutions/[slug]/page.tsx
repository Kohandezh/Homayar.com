import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale, locales } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { solutionSlugs, type SolutionSlug } from "@/lib/site";
import { Section } from "@/components/ui/Section";
import { CheckList, Chip } from "@/components/ui/Cards";
import { CtaBand } from "@/components/site/CtaBand";
import { Icon } from "@/components/ui/Icon";
import { KpiGrid, RootCauses, ScoreBars, Stages, TrendChart } from "@/components/mockups/parts";

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
  const m = dict.mock;
  const mockLabel = dict.common.decorativeMockup;

  return (
    <>
      <section className="border-b border-border bg-surface/60 py-12 md:py-16">
        <div className="container-x">
          <Link href={`/${locale}/solutions`} className="inline-flex min-h-11 items-center gap-1 text-sm font-semibold text-primary hover:underline">
            <Icon name={locale === "fa" ? "arrowRight" : "arrowLeft"} size={16} />
            {dict.nav.solutions}
          </Link>
          <div className="mt-4 grid gap-10 lg:grid-cols-[1.1fr_1fr] lg:items-center">
            <div>
              <p className="eyebrow">{role.name}</p>
              <h1 className="mt-3 text-3xl font-bold md:text-5xl">{role.hero.title}</h1>
              <p className="mt-4 max-w-2xl text-lg text-ink-2">{role.hero.text}</p>
              <Link href={`/${locale}/contact`} className="btn btn-primary mt-6">
                {dict.solutions.cta}
              </Link>
            </div>
            <div className="mock-window p-4" role="img" aria-label={mockLabel}>
              <div aria-hidden="true" className="flex flex-col gap-3">
                {slug === "qa-managers" ? (
                  <div className="rounded-xl border border-border bg-bg p-3">
                    <p className="mb-2 text-[10px] font-semibold text-ink-2">{m.qc}</p>
                    <ScoreBars mock={m} locale={locale} />
                  </div>
                ) : slug === "supervisors" ? (
                  <>
                    <div className="rounded-xl border border-border bg-bg p-3">
                      <p className="mb-2 text-[10px] font-semibold text-ink-2">{m.problems}</p>
                      <RootCauses mock={m} />
                    </div>
                    <div className="rounded-xl border border-border bg-bg p-3">
                      <p className="mb-2 text-[10px] font-semibold text-ink-2">{m.qc}</p>
                      <ScoreBars mock={m} locale={locale} limit={4} />
                    </div>
                  </>
                ) : slug === "contact-center-operations" ? (
                  <>
                    <KpiGrid mock={m} />
                    <TrendChart mock={m} />
                  </>
                ) : (
                  <Stages mock={m} />
                )}
              </div>
            </div>
          </div>
        </div>
      </section>

      <Section>
        <div className="grid gap-6 lg:grid-cols-[1fr_1.3fr]">
          <div className="card p-6 md:p-8">
            <h2 className="text-xl font-bold">{role.needs.title}</h2>
            <ul className="mt-5 flex flex-col gap-4">
              {role.needs.items.map((n, i) => (
                <li key={n} className="flex gap-3">
                  <span className="inline-grid size-7 shrink-0 place-items-center rounded-full bg-sand text-sm font-bold">{locale === "fa" ? ["۱", "۲", "۳"][i] : i + 1}</span>
                  <span className="text-ink-2">{n}</span>
                </li>
              ))}
            </ul>
          </div>
          <div className="rounded-[20px] border border-primary/30 bg-primary-soft/40 p-6 md:p-8">
            <h2 className="text-xl font-bold">{role.gets.title}</h2>
            <div className="mt-5">
              <CheckList items={role.gets.items} />
            </div>
            <div className="mt-6 border-t border-primary/20 pt-5">
              <div className="flex items-center gap-2">
                <h3 className="font-semibold">{role.roadmap.title}</h3>
                <Chip tone="amber">{dict.common.comingSoon}</Chip>
              </div>
              <ul className="mt-3 flex flex-col gap-2 text-ink-2">
                {role.roadmap.items.map((r) => (
                  <li key={r} className="flex items-start gap-2">
                    <span className="mt-2 size-1.5 shrink-0 rounded-full bg-accent" />
                    {r}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </Section>

      <CtaBand locale={locale} dict={dict} />
    </>
  );
}
