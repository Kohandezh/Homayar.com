import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero } from "@/components/ui/Section";
import { CommandCta } from "@/components/site/CommandCta";
import { RoleOrbit } from "@/components/story/RoleOrbit";

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
    // The roles stand in a ring around a glowing core. Scrolling turns the ring (design-system/SCENE-RULES.md).
    <div className="relative" data-scene="orbit" data-testid="orbit">
      <div className="naked">
        <PageHero eyebrow={s.hero.eyebrow} title={s.hero.title} text={s.hero.text} align="center" />
      </div>
      <RoleOrbit dict={dict} locale={locale} />
      <CommandCta locale={locale} dict={dict} naked />
    </div>
  );
}
