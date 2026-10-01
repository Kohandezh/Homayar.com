import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero, Section, SectionHeading } from "@/components/ui/Section";
import { CommandCta } from "@/components/site/CommandCta";
import { LiveDemo } from "@/components/demo/LiveDemo";
import { ConsoleAssembly } from "@/components/story/ConsoleAssembly";

export async function generateMetadata({ params }: PageProps<"/[locale]/product">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return pageMetadata({ locale, path: "/product", title: dict.product.meta.title, description: dict.product.meta.description, siteName: dict.brand });
}

export default async function ProductPage({ params }: PageProps<"/[locale]/product">) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);
  const p = dict.product;

  return (
    <>
      <PageHero eyebrow={p.hero.eyebrow} title={p.hero.title} text={p.hero.text} />
      <ConsoleAssembly dict={dict} locale={locale} />
      <Section tone="raised">
        <SectionHeading eyebrow={p.console.demoEyebrow} title={p.console.demoTitle} text={p.console.demoText} split />
        <div className="mt-10">
          <LiveDemo demo={dict.demo} mock={dict.mock} locale={locale} brand={dict.brand} frame="window" />
        </div>
      </Section>
      <CommandCta locale={locale} dict={dict} />
    </>
  );
}
