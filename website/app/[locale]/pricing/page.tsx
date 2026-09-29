import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero, Section } from "@/components/ui/Section";
import { Chip, Notice } from "@/components/ui/Cards";
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
    <>
      <PageHero eyebrow={p.hero.eyebrow} title={p.hero.title} text={p.hero.text}>
        <Notice title={dict.common.inProgress}>{p.notice}</Notice>
      </PageHero>

      <Section scene="field">
        <div className="mb-6 flex flex-wrap gap-3" data-reveal-group>
          {p.deployments.map((d, i) => (
            <div key={d.name} className="flex items-center gap-3 rounded-full border border-border-2 bg-surface py-2 pe-4 ps-2">
              <span className="inline-grid size-8 place-items-center rounded-full bg-surface-2 text-accent-text">
                <Icon name={i === 0 ? "cloud" : "server"} size={16} />
              </span>
              <span className="font-semibold">{d.name}</span>
              <span className="text-sm text-ink-2">{d.text}</span>
            </div>
          ))}
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          {p.plans.map((plan, i) => (
            <div key={plan.name} className={`panel flex flex-col p-6 md:p-8 ${i === 2 ? "panel-glow" : ""}`} data-depth={String(200 + i * 80)}>
              <h2 className="text-2xl font-bold">{plan.name}</h2>
              <p className="mt-1 text-ink-2">{plan.text}</p>
              <div className="mt-8">
                <Chip tone={i === 2 ? "accent" : "default"}>{plan.amount}</Chip>
              </div>
              <Link href={`/${locale}/contact`} className={`btn mt-8 ${i === 2 ? "btn-primary" : "btn-ghost"}`}>
                {p.cta}
              </Link>
            </div>
          ))}
        </div>
      </Section>
    </>
  );
}
