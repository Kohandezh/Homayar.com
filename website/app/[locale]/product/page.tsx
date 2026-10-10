import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero } from "@/components/ui/Section";
import { CommandCta } from "@/components/site/CommandCta";
import { ProductGallery } from "@/components/story/ProductGallery";

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
    // A gallery visit: walk down the hall, stop at each painting, turn to it and read it (design-system/SCENE-RULES.md).
    <div className="relative" data-scene="gallery" data-testid="gallery">
      <div className="naked">
        <PageHero eyebrow={p.hero.eyebrow} title={p.hero.title} text={p.hero.text} align="center" />
      </div>
      <ProductGallery dict={dict} locale={locale} />
      <CommandCta locale={locale} dict={dict} naked />
    </div>
  );
}
