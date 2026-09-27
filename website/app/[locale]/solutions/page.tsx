import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { solutionSlugs } from "@/lib/site";
import { PageHero, Section } from "@/components/ui/Section";
import { CtaBand } from "@/components/site/CtaBand";
import { Icon } from "@/components/ui/Icon";

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

  return (
    <>
      <PageHero eyebrow={s.hero.eyebrow} title={s.hero.title} text={s.hero.text} />
      <Section>
        <div className="grid gap-4 md:grid-cols-2">
          {solutionSlugs.map((slug, i) => {
            const role = s.roles[slug];
            return (
              <Link
                key={slug}
                href={`/${locale}/solutions/${slug}`}
                className={`group flex flex-col gap-4 rounded-[20px] border p-6 transition-colors md:p-8 ${
                  i === 0 ? "border-ink-teal bg-ink-teal text-cream hover:bg-[#0f302d]" : "card hover:border-primary"
                }`}
              >
                <span className={`inline-grid size-12 place-items-center rounded-xl ${i === 0 ? "bg-cream/15 text-accent" : "bg-primary-soft text-primary"}`}>
                  <Icon name={role.icon} size={24} />
                </span>
                <h2 className="text-2xl font-bold">{role.name}</h2>
                <p className={i === 0 ? "text-cream/85" : "text-ink-2"}>{role.short}</p>
                <span className={`mt-auto inline-flex items-center gap-1 font-semibold ${i === 0 ? "text-accent" : "text-primary"}`}>
                  {dict.common.learnMore}
                  <Icon name={locale === "fa" ? "arrowLeft" : "arrowRight"} size={16} className="transition-transform group-hover:translate-x-0.5 rtl:group-hover:-translate-x-0.5" />
                </span>
              </Link>
            );
          })}
        </div>
      </Section>
      <CtaBand locale={locale} dict={dict} />
    </>
  );
}
