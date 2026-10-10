import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero } from "@/components/ui/Section";
import { PlanLift } from "@/components/story/PlanLift";
import { Icon } from "@/components/ui/Icon";

export async function generateMetadata({ params }: PageProps<"/[locale]/pricing">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return pageMetadata({ locale, path: "/pricing", title: dict.pricing.meta.title, description: dict.pricing.meta.description, siteName: dict.brand });
}

export default async function PricingPage({ params }: PageProps<"/[locale]/pricing">) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);
  const p = dict.pricing;

  return (
    // A lift straight up. One plan waits on each floor (design-system/SCENE-RULES.md).
    <div className="relative" data-scene="lift" data-testid="lift">
      <div className="naked">
        <PageHero eyebrow={p.hero.eyebrow} title={p.hero.title} text={p.hero.text} align="center">
          <div className="mx-auto flex max-w-2xl flex-col items-center gap-5">
            <p className="flex items-start gap-3 border-s-2 border-accent ps-4 text-start text-sm text-ink-2">
              <Icon name="clock" size={18} className="mt-0.5 shrink-0 text-accent-text" />
              <span>
                <span className="font-semibold text-ink">{dict.common.inProgress}. </span>
                {p.notice}
              </span>
            </p>
            <ul className="flex flex-wrap justify-center gap-x-8 gap-y-2">
              {p.deployments.map((d, i) => (
                <li key={d.name} className="flex items-center gap-2 text-sm">
                  <Icon name={i === 0 ? "cloud" : "server"} size={16} className="text-accent-text" />
                  <span className="font-semibold">{d.name}</span>
                  <span className="text-ink-2">{d.text}</span>
                </li>
              ))}
            </ul>
          </div>
        </PageHero>
      </div>
      <PlanLift dict={dict} locale={locale} />
    </div>
  );
}
