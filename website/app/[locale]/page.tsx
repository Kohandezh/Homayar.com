import type { Metadata } from "next";
import Link from "next/link";
import { getDictionary } from "@/i18n";
import { isLocale, type Locale } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { notFound } from "next/navigation";
import { Section, SectionHeading } from "@/components/ui/Section";
import { FeatureCard, Chip } from "@/components/ui/Cards";
import { Icon } from "@/components/ui/Icon";
import { AppWindow } from "@/components/mockups/AppWindow";
import { PhoneFrame } from "@/components/mockups/PhoneFrame";
import { CtaBand } from "@/components/site/CtaBand";

export async function generateMetadata({ params }: PageProps<"/[locale]">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return {
    ...pageMetadata({ locale, path: "", title: dict.home.meta.title, description: dict.home.meta.description, siteName: dict.brand }),
    title: { absolute: dict.home.meta.title },
  };
}

export default async function HomePage({ params }: PageProps<"/[locale]">) {
  const { locale: raw } = await params;
  if (!isLocale(raw)) notFound();
  const locale: Locale = raw;
  const dict = await getDictionary(locale);
  const h = dict.home;
  const base = `/${locale}`;

  return (
    <>
      {/* Hero: copy on one side, the console and a phone on the other. */}
      <section className="relative overflow-hidden">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[520px]"
          style={{ background: "radial-gradient(60% 70% at 80% 10%, rgba(217,119,6,0.16), transparent 70%), radial-gradient(50% 60% at 10% 90%, rgba(15,118,110,0.12), transparent 70%)" }}
        />
        <div className="container-x grid items-center gap-10 py-12 md:py-16 lg:grid-cols-[1.05fr_1fr] lg:py-24">
          <div className="flex flex-col gap-6">
            <p className="eyebrow">{h.hero.eyebrow}</p>
            <h1 className="text-4xl font-extrabold md:text-5xl lg:text-[3.5rem] lg:leading-[1.15]">{h.hero.title}</h1>
            <p className="max-w-xl text-lg text-ink-2 md:text-xl">{h.hero.text}</p>
            <div className="flex flex-wrap gap-3">
              <Link href={`${base}/contact`} className="btn btn-primary">
                {h.hero.primary}
                <Icon name={locale === "fa" ? "arrowLeft" : "arrowRight"} size={18} />
              </Link>
              <Link href={`${base}/product`} className="btn btn-secondary">
                {h.hero.secondary}
              </Link>
            </div>
            <ul className="flex flex-wrap gap-2 pt-2">
              {h.hero.facts.map((f) => (
                <li key={f}>
                  <Chip>{f}</Chip>
                </li>
              ))}
            </ul>
          </div>
          <div className="relative mx-auto w-full max-w-[640px]">
            <AppWindow dict={dict} locale={locale} label={dict.common.decorativeMockup} />
            <div className="pointer-events-none absolute -bottom-6 -end-2 hidden origin-bottom scale-[0.78] sm:block md:-end-6 md:scale-90">
              <PhoneFrame dict={dict} locale={locale} label={dict.common.decorativeMockup} />
            </div>
          </div>
        </div>
      </section>

      {/* Four outputs */}
      <Section tone="sand" className="pt-20 sm:pt-28">
        <SectionHeading eyebrow={h.outputs.eyebrow} title={h.outputs.title} text={h.outputs.text} />
        <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {h.outputs.items.map((item) => (
            <FeatureCard key={item.title} icon={item.icon} title={item.title} text={item.text} />
          ))}
        </div>
      </Section>

      {/* Pipeline strip */}
      <Section>
        <SectionHeading eyebrow={h.pipeline.eyebrow} title={h.pipeline.title} />
        <ol className="mt-10 grid gap-4 md:grid-cols-4">
          {h.pipeline.steps.map((step, i) => (
            <li key={step.title} className="relative flex flex-col gap-2 rounded-2xl border border-border bg-surface p-5">
              <span className="inline-grid size-9 place-items-center rounded-full bg-ink-teal text-sm font-bold text-cream">
                {locale === "fa" ? ["۱", "۲", "۳", "۴"][i] : i + 1}
              </span>
              <h3 className="text-lg font-semibold">{step.title}</h3>
              <p className="text-ink-2">{step.text}</p>
            </li>
          ))}
        </ol>
        <div className="mt-6">
          <Link href={`${base}/how-it-works`} className="inline-flex min-h-11 items-center gap-1 font-semibold text-primary hover:underline">
            {h.pipeline.link}
            <Icon name={locale === "fa" ? "arrowLeft" : "arrowRight"} size={16} />
          </Link>
        </div>
      </Section>

      {/* Product bento */}
      <Section tone="sand">
        <SectionHeading eyebrow={h.product.eyebrow} title={h.product.title} />
        <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {h.product.items.map((item) => (
            <div key={item.title} className="card flex items-start gap-4 p-5">
              <span className="inline-grid size-10 shrink-0 place-items-center rounded-xl bg-ink-teal text-cream">
                <Icon name={item.icon} size={20} />
              </span>
              <div>
                <h3 className="font-semibold">{item.title}</h3>
                <p className="mt-1 text-sm text-ink-2">{item.text}</p>
              </div>
            </div>
          ))}
        </div>
        <div className="mt-6">
          <Link href={`${base}/product`} className="btn btn-secondary">
            {h.product.link}
          </Link>
        </div>
      </Section>

      {/* Deployment */}
      <Section>
        <div className="grid gap-10 lg:grid-cols-[1fr_1.4fr] lg:items-start">
          <div>
            <SectionHeading eyebrow={h.deployment.eyebrow} title={h.deployment.title} />
            <Link href={`${base}/deployment`} className="btn btn-primary mt-6">
              {h.deployment.link}
            </Link>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            {h.deployment.items.map((item, i) => (
              <div key={item.title} className={`rounded-2xl p-5 ${i === 1 ? "bg-ink-teal text-cream" : "card"}`}>
                <span className={i === 1 ? "text-accent" : "text-primary"}>
                  <Icon name={i === 0 ? "cloud" : i === 1 ? "server" : "layers"} size={26} />
                </span>
                <h3 className="mt-3 text-lg font-semibold">{item.title}</h3>
                <p className={`mt-2 text-sm ${i === 1 ? "text-cream/85" : "text-ink-2"}`}>{item.text}</p>
              </div>
            ))}
          </div>
        </div>
      </Section>

      {/* Trust */}
      <Section tone="sand">
        <SectionHeading eyebrow={h.trust.eyebrow} title={h.trust.title} />
        <div className="mt-10 grid gap-4 sm:grid-cols-2">
          {h.trust.items.map((item) => (
            <div key={item.title} className="card flex items-start gap-4 p-5">
              <span className="inline-grid size-11 shrink-0 place-items-center rounded-xl bg-primary-soft text-primary">
                <Icon name={item.icon} size={22} />
              </span>
              <div>
                <h3 className="text-lg font-semibold">{item.title}</h3>
                <p className="mt-1 text-ink-2">{item.text}</p>
              </div>
            </div>
          ))}
        </div>
        <div className="mt-6">
          <Link href={`${base}/security`} className="inline-flex min-h-11 items-center gap-1 font-semibold text-primary hover:underline">
            {h.trust.link}
            <Icon name={locale === "fa" ? "arrowLeft" : "arrowRight"} size={16} />
          </Link>
        </div>
      </Section>

      <CtaBand locale={locale} dict={dict} />
    </>
  );
}
