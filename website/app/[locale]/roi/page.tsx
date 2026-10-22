import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero } from "@/components/ui/Section";
import { CommandCta } from "@/components/site/CommandCta";
import { RoiCalculator } from "@/components/story/RoiCalculator";

export async function generateMetadata({ params }: PageProps<"/[locale]/roi">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return pageMetadata({ locale, path: "/roi", title: dict.roi.meta.title, description: dict.roi.meta.description, siteName: dict.brand });
}

export default async function RoiPage({ params }: PageProps<"/[locale]/roi">) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);
  const r = dict.roi;

  return (
    // Two towers of hours stand on the grid: today and with Homayar. The gap between them is the saved time (design-system/SCENE-RULES.md).
    <div className="relative" data-scene="stacks" data-testid="stacks">
      <div className="naked">
        <PageHero eyebrow={r.hero.eyebrow} title={r.hero.title} text={r.hero.text} />
      </div>

      <div className="container-x naked pb-8">
        <div className="roi-column">
          <RoiCalculator copy={r} locale={locale} />

          <section className="mt-20" data-reveal>
            <h2 className="text-2xl font-bold md:text-3xl">{r.notes.title}</h2>
            <ul className="mt-6 divide-y divide-border-2 border-y border-border-2">
              {r.notes.items.map((item) => (
                <li key={item} className="flex gap-4 py-4 text-ink-2">
                  <span className="glow-dot mt-2.5" aria-hidden="true" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="mt-20 border-s-2 border-accent ps-6" data-reveal>
            <h2 className="text-2xl font-bold md:text-3xl">{r.product.title}</h2>
            <p className="mt-3 text-lg text-ink-2">{r.product.text}</p>
          </section>
        </div>
      </div>

      <CommandCta locale={locale} dict={dict} naked />
    </div>
  );
}
