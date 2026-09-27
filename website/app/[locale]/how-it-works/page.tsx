import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale, localizeDigits } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { PageHero, Section, SectionHeading } from "@/components/ui/Section";
import { CtaBand } from "@/components/site/CtaBand";
import { Icon } from "@/components/ui/Icon";

export async function generateMetadata({ params }: PageProps<"/[locale]/how-it-works">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return pageMetadata({ locale, path: "/how-it-works", title: dict.howItWorks.meta.title, description: dict.howItWorks.meta.description, siteName: dict.brand });
}

const stepIcons = ["upload", "mic", "users", "sparkles", "layout"];

export default async function HowItWorksPage({ params }: PageProps<"/[locale]/how-it-works">) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);
  const h = dict.howItWorks;

  return (
    <>
      <PageHero eyebrow={h.hero.eyebrow} title={h.hero.title} text={h.hero.text} />

      {/* Vertical stepper with a rail */}
      <Section>
        <ol className="relative mx-auto max-w-3xl">
          <div aria-hidden="true" className="absolute inset-y-4 start-[22px] w-0.5 bg-border" />
          {h.steps.map((step, i) => (
            <li key={step.title} className="relative flex gap-5 pb-10 last:pb-0">
              <span className="relative z-10 inline-grid size-11 shrink-0 place-items-center rounded-full border-4 border-bg bg-ink-teal text-cream">
                <Icon name={stepIcons[i]} size={18} />
              </span>
              <div className="card flex-1 p-5 md:p-6">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded-full bg-sand px-2.5 py-0.5 text-xs font-semibold text-ink-2">
                    {localizeDigits(i + 1, locale)}
                  </span>
                  <span className="rounded-full bg-primary-soft px-2.5 py-0.5 text-xs font-semibold text-primary">{step.stage}</span>
                </div>
                <h2 className="mt-3 text-xl font-semibold">{step.title}</h2>
                <p className="mt-2 text-ink-2">{step.text}</p>
              </div>
            </li>
          ))}
        </ol>
      </Section>

      {/* States */}
      <Section tone="dark">
        <SectionHeading eyebrow={h.states.eyebrow} title={h.states.title} text={h.states.text} />
        <dl className="mt-10 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {h.states.items.map((item, i) => (
            <div key={item.label} className="rounded-2xl border border-cream/15 bg-cream/5 p-5">
              <dt className="flex items-center gap-2 font-semibold">
                <span
                  className={`size-2.5 rounded-full ${
                    i === 0 ? "bg-cream/40" : i === 1 ? "bg-cream/70" : i === 2 ? "bg-accent" : i === 3 ? "bg-[#fbbf24]" : i === 4 ? "bg-cream/60" : "bg-terracotta"
                  }`}
                  aria-hidden="true"
                />
                {item.label}
              </dt>
              <dd className="mt-2 text-sm text-cream/80">{item.text}</dd>
            </div>
          ))}
        </dl>
      </Section>

      {/* Policy */}
      <Section>
        <div className="grid gap-8 rounded-[24px] border border-border bg-surface p-6 md:grid-cols-[1fr_auto] md:items-center md:p-10">
          <div>
            <p className="eyebrow">{h.policy.eyebrow}</p>
            <h2 className="mt-3 text-2xl font-bold md:text-3xl">{h.policy.title}</h2>
            <p className="mt-4 max-w-2xl text-ink-2">{h.policy.text}</p>
          </div>
          <Link href={`/${locale}/security#routing`} className="btn btn-primary">
            {h.policy.link}
          </Link>
        </div>
      </Section>

      <CtaBand locale={locale} dict={dict} />
    </>
  );
}
