import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDictionary } from "@/i18n";
import { isLocale, localizeDigits } from "@/lib/i18n";
import { pageMetadata } from "@/lib/metadata";
import { Section } from "@/components/ui/Section";
import { FeatureCard } from "@/components/ui/Cards";
import { CtaBand } from "@/components/site/CtaBand";

export async function generateMetadata({ params }: PageProps<"/[locale]/about">): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const dict = await getDictionary(locale);
  return pageMetadata({ locale, path: "/about", title: dict.about.meta.title, description: dict.about.meta.description, siteName: dict.brand });
}

export default async function AboutPage({ params }: PageProps<"/[locale]/about">) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);
  const a = dict.about;

  return (
    <>
      {/* Editorial hero on the dark anchor color */}
      <section className="bg-ink-teal py-16 text-cream md:py-24">
        <div className="container-x grid gap-8 lg:grid-cols-[1.2fr_1fr] lg:items-end">
          <div>
            <p className="eyebrow text-accent">{a.hero.eyebrow}</p>
            <h1 className="mt-4 text-3xl font-bold md:text-5xl">{a.hero.title}</h1>
          </div>
          <p className="text-lg text-cream/85">{a.hero.text}</p>
        </div>
      </section>

      <Section>
        <h2 className="text-2xl font-bold md:text-3xl">{a.principles.title}</h2>
        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {a.principles.items.map((p) => (
            <FeatureCard key={p.title} icon={p.icon} title={p.title} text={p.text} />
          ))}
        </div>
      </Section>

      <Section tone="sand">
        <div className="grid gap-10 lg:grid-cols-2">
          <div>
            <h2 className="text-2xl font-bold md:text-3xl">{a.who.title}</h2>
            <p className="mt-4 text-lg text-ink-2">{a.who.text}</p>
          </div>
          <div>
            <h2 className="text-2xl font-bold md:text-3xl">{a.howWeWork.title}</h2>
            <ol className="mt-4 flex flex-col gap-3">
              {a.howWeWork.steps.map((s, i) => (
                <li key={s.title} className="card flex gap-4 p-4">
                  <span className="inline-grid size-9 shrink-0 place-items-center rounded-full bg-ink-teal text-sm font-bold text-cream">{localizeDigits(i + 1, locale)}</span>
                  <div>
                    <h3 className="font-semibold">{s.title}</h3>
                    <p className="mt-1 text-sm text-ink-2">{s.text}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </Section>

      <Section>
        <div className="mx-auto max-w-3xl rounded-[24px] border border-accent/50 bg-[#fff6e5] p-6 md:p-10">
          <h2 className="text-2xl font-bold">{a.status.title}</h2>
          <p className="mt-3 text-lg text-ink-2">{a.status.text}</p>
        </div>
      </Section>

      <CtaBand locale={locale} dict={dict} />
    </>
  );
}
