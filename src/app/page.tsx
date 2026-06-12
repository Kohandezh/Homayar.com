import type { Metadata } from "next";
import Image from "next/image";
import type { ReactElement } from "react";
import { ButtonLink, Eyebrow, SectionHeading } from "@/components/ui";
import { ArrowRightIcon, CheckIcon } from "@/components/icons";
import { ProductCard } from "@/components/product-card";
import { getProducts, getCategories } from "@/lib/catalog";
import { SITE, absoluteUrl } from "@/lib/site";
import siteCopy from "@content/site-copy.json";
import {
  CoverageVisual,
  AgentsVisual,
  CoachingVisual,
  ComplianceVisual,
  InsightsVisual,
  EnterpriseVisual,
} from "@/components/home/feature-visuals";

const home = siteCopy.home;

export const metadata: Metadata = {
  title: `${SITE.name} — ${home.heroEyebrow}`,
  description: home.heroSub,
  openGraph: {
    title: `${SITE.name} — ${home.heroEyebrow}`,
    description: home.heroSub,
    images: [{ url: absoluteUrl("/hero.png"), width: 1536, height: 1024 }],
  },
};

const INDUSTRIES = [
  "Banking",
  "Healthcare",
  "Telecom",
  "Insurance",
  "Retail",
  "Government",
];

const FEATURE_VISUALS: ReactElement[] = [
  <CoverageVisual key="coverage" />,
  <AgentsVisual key="agents" />,
  <CoachingVisual key="coaching" />,
  <ComplianceVisual key="compliance" />,
  <InsightsVisual key="insights" />,
  <EnterpriseVisual key="enterprise" />,
];

export default async function HomePage() {
  const [products, categories] = await Promise.all([
    getProducts(),
    getCategories(),
  ]);
  const categoryNames = new Map(categories.map((c) => [c.slug, c.name]));
  const featured = products
    .filter((p) => p.recommended && p.category !== "platform-plans")
    .slice(0, 3);

  return (
    <>
      {/* ── 1. Hero ── */}
      <section className="relative overflow-hidden border-b border-line">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(55% 60% at 50% -5%, rgba(34,211,238,0.14), transparent 65%), radial-gradient(40% 50% at 85% 20%, rgba(139,92,246,0.12), transparent 60%), radial-gradient(35% 45% at 10% 30%, rgba(45,212,191,0.07), transparent 60%)",
          }}
        />
        <div className="container-site relative pb-20 pt-20 sm:pb-24 sm:pt-28">
          <div className="mx-auto max-w-3xl text-center">
            <div className="animate-fade-up">
              <Eyebrow>{home.heroEyebrow}</Eyebrow>
            </div>
            <h1
              className="animate-fade-up font-display text-4xl font-semibold tracking-tight text-ink sm:text-6xl"
              style={{ animationDelay: "80ms" }}
            >
              Every call has a <span className="text-gradient">companion</span>.
            </h1>
            <p
              className="animate-fade-up mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-ink-soft"
              style={{ animationDelay: "160ms" }}
            >
              {home.heroSub}
            </p>
            <div
              className="animate-fade-up mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row"
              style={{ animationDelay: "240ms" }}
            >
              <ButtonLink href="/contact" variant="primary" size="lg">
                {home.heroPrimaryCta}
                <ArrowRightIcon width={18} height={18} />
              </ButtonLink>
              <ButtonLink href="/platform" variant="secondary" size="lg">
                {home.heroSecondaryCta}
              </ButtonLink>
            </div>
          </div>

          <div
            className="animate-fade-up mx-auto mt-16 max-w-5xl"
            style={{ animationDelay: "320ms" }}
          >
            <div className="card-surface glow-accent overflow-hidden p-2 sm:p-3">
              <Image
                src="/hero.png"
                alt="Homayar dashboard showing AI agents analyzing and scoring call-center conversations"
                width={1536}
                height={1024}
                priority
                sizes="(max-width: 1024px) 100vw, 64rem"
                className="w-full rounded-xl border border-line"
              />
            </div>
          </div>
        </div>
      </section>

      {/* ── 2. Trust strip ── */}
      <section className="border-b border-line bg-surface">
        <div className="container-site py-10">
          <p className="text-center text-sm text-ink-muted">{home.logosLabel}</p>
          <ul className="mt-6 flex flex-wrap items-center justify-center gap-3">
            {INDUSTRIES.map((industry) => (
              <li key={industry}>
                <span className="inline-flex items-center rounded-full border border-line bg-raised px-4 py-1.5 font-display text-sm font-medium tracking-wide text-ink-soft">
                  {industry}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* ── 3. How it works ── */}
      <section className="border-b border-line">
        <div className="container-site py-20 sm:py-24">
          <SectionHeading
            eyebrow="How it works"
            title="From raw recordings to scored insight in three steps"
            description="No rip-and-replace, no agent-side software. Homayar sits alongside the call center you already run."
            center
          />
          <div className="mt-14 grid gap-6 md:grid-cols-3">
            {home.steps.map((step, i) => (
              <article key={step.title} className="card-surface relative p-7">
                <p className="font-mono text-sm font-semibold text-accent">
                  {String(i + 1).padStart(2, "0")}
                </p>
                <h3 className="mt-4 font-display text-xl font-semibold tracking-tight text-ink">
                  {step.title}
                </h3>
                <p className="mt-3 text-sm leading-relaxed text-ink-muted">
                  {step.description}
                </p>
                {i < home.steps.length - 1 && (
                  <ArrowRightIcon
                    aria-hidden
                    width={20}
                    height={20}
                    className="absolute -right-3.5 top-1/2 hidden -translate-y-1/2 text-ink-faint md:block"
                  />
                )}
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* ── 4. Feature sections ── */}
      <section className="border-b border-line">
        <div className="container-site space-y-20 py-20 sm:space-y-24 sm:py-24">
          {home.featureSections.map((feature, i) => (
            <div
              key={feature.title}
              className="grid items-center gap-10 lg:grid-cols-2 lg:gap-16"
            >
              <div className={i % 2 === 1 ? "lg:order-2" : undefined}>
                <Eyebrow>{feature.eyebrow}</Eyebrow>
                <h2 className="font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
                  {feature.title}
                </h2>
                <p className="mt-4 text-lg leading-relaxed text-ink-soft">
                  {feature.description}
                </p>
                <ul className="mt-6 space-y-3">
                  {feature.bullets.map((bullet) => (
                    <li key={bullet} className="flex items-start gap-3">
                      <CheckIcon
                        width={18}
                        height={18}
                        className="mt-1 shrink-0 text-accent"
                      />
                      <span className="text-sm leading-relaxed text-ink-soft">
                        {bullet}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
              <div className={i % 2 === 1 ? "lg:order-1" : undefined}>
                {FEATURE_VISUALS[i]}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ── 5. Metrics band ── */}
      <section className="border-b border-line bg-surface">
        <div className="container-site py-20 sm:py-24">
          <div className="card-surface grid gap-px overflow-hidden bg-line sm:grid-cols-2 lg:grid-cols-4">
            {home.metricsBand.map((metric) => (
              <div key={metric.label} className="bg-surface p-8 text-center">
                <p className="font-mono text-4xl font-semibold tracking-tight text-accent">
                  {metric.value}
                </p>
                <p className="mt-3 text-sm leading-relaxed text-ink-muted">
                  {metric.label}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── 6. Featured modules ── */}
      <section className="border-b border-line">
        <div className="container-site py-20 sm:py-24">
          <div className="flex flex-col gap-6 sm:flex-row sm:items-end sm:justify-between">
            <SectionHeading
              eyebrow="Modules"
              title="Start with the modules teams reach for first"
              description="Every Homayar capability ships as a module you can add when you need it. These three are where most teams begin."
            />
            <ButtonLink href="/catalog" variant="secondary" className="shrink-0">
              Browse the full catalog
              <ArrowRightIcon width={16} height={16} />
            </ButtonLink>
          </div>
          <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {featured.map((product) => (
              <ProductCard
                key={product.slug}
                product={product}
                categoryName={categoryNames.get(product.category)}
              />
            ))}
          </div>
        </div>
      </section>

      {/* ── 7. Testimonial ── */}
      <section className="border-b border-line bg-surface">
        <div className="container-site py-20 sm:py-24">
          <figure className="mx-auto max-w-3xl text-center">
            <p
              aria-hidden
              className="font-display text-6xl leading-none text-accent/40"
            >
              &ldquo;
            </p>
            <blockquote className="mt-2 font-display text-2xl font-medium leading-snug tracking-tight text-ink sm:text-3xl">
              {home.testimonial.quote}
            </blockquote>
            <figcaption className="mt-8">
              <p className="font-semibold text-ink">{home.testimonial.author}</p>
              <p className="mt-1 text-sm text-ink-muted">
                {home.testimonial.role}
              </p>
            </figcaption>
          </figure>
        </div>
      </section>

      {/* ── 8. CTA band ── */}
      <section>
        <div className="container-site py-20 sm:py-24">
          <div className="card-surface glow-accent relative overflow-hidden px-6 py-16 text-center sm:px-12">
            <div
              aria-hidden
              className="pointer-events-none absolute inset-0"
              style={{
                background:
                  "radial-gradient(60% 90% at 50% 0%, rgba(34,211,238,0.12), transparent 65%), radial-gradient(45% 60% at 80% 100%, rgba(139,92,246,0.10), transparent 60%)",
              }}
            />
            <div className="relative mx-auto max-w-2xl">
              <h2 className="font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
                {home.ctaBand.title}
              </h2>
              <p className="mt-4 text-lg leading-relaxed text-ink-soft">
                {home.ctaBand.sub}
              </p>
              <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
                <ButtonLink href="/contact" variant="primary" size="lg">
                  {home.ctaBand.cta}
                  <ArrowRightIcon width={18} height={18} />
                </ButtonLink>
                <ButtonLink href="/platform" variant="secondary" size="lg">
                  {home.heroSecondaryCta}
                </ButtonLink>
              </div>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
