import type { Metadata } from "next";
import Link from "next/link";
import type { Product } from "@/lib/types";
import { formatPrice } from "@/lib/types";
import { getCategories, getFaq, getProducts } from "@/lib/catalog";
import { Badge, ButtonLink, PageHero, SectionHeading } from "@/components/ui";
import {
  ArrowRightIcon,
  CheckIcon,
  ChevronDownIcon,
} from "@/components/icons";
import { AddToCartButton } from "@/components/add-to-cart";
import { ProductCard } from "@/components/product-card";

export const metadata: Metadata = {
  title: "Pricing — Homayar",
  description:
    "Transparent plans for agentic AI voice analysis. Starter from $499/mo, Professional at $1,499/mo, and Enterprise with custom volume pricing. Extend any plan with specialized modules.",
};

const PLAN_ORDER = [
  "homayar-starter",
  "homayar-professional",
  "homayar-enterprise",
];

const BILLING_NOTES = [
  {
    term: "Monthly",
    note: "Platform plans and most modules bill per month, sized by analyzed call volume. Upgrade anytime; cancel before the next cycle with no penalty.",
  },
  {
    term: "Yearly",
    note: "Annual items — training programs and managed services — bill once per year and include a 30-day evaluation window with a prorated exit.",
  },
  {
    term: "One-time",
    note: "Implementation packages and fixed-scope services bill a single time, with the scope agreed in writing before work begins.",
  },
];

function PlanCard({ plan, featured }: { plan: Product; featured: boolean }) {
  return (
    <article
      className={`card-surface relative flex flex-col p-6 sm:p-8 ${
        featured ? "glow-accent border-accent/40 lg:-my-4 lg:py-12" : ""
      }`}
    >
      {featured && (
        <div className="absolute -top-3 left-1/2 -translate-x-1/2">
          <Badge tone="accent">Most popular</Badge>
        </div>
      )}
      <h3 className="font-display text-xl font-semibold tracking-tight text-ink">
        {plan.name}
      </h3>
      <p className="mt-1.5 text-sm text-ink-muted">{plan.tagline}</p>
      <p className="mt-5 font-mono text-3xl font-semibold text-ink sm:text-4xl">
        {formatPrice(plan.priceUsd, plan.billingType)}
      </p>
      <p className="mt-1 text-xs text-ink-muted">
        {plan.billingType === "custom"
          ? "Volume-based annual contract, quoted in USD"
          : "Billed monthly, sized by analyzed call volume"}
      </p>

      <div className="mt-6">
        <AddToCartButton product={plan} size="lg" />
      </div>

      <ul className="mt-8 space-y-3 border-t border-line pt-6">
        {plan.features.map((feature) => (
          <li key={feature} className="flex items-start gap-3">
            <CheckIcon
              width={16}
              height={16}
              className={`mt-0.5 shrink-0 ${featured ? "text-accent" : "text-ink-muted"}`}
            />
            <span className="text-sm leading-relaxed text-ink-soft">
              {feature}
            </span>
          </li>
        ))}
      </ul>
    </article>
  );
}

export default async function PricingPage() {
  const [products, categories] = await Promise.all([
    getProducts(),
    getCategories(),
  ]);

  const plans = PLAN_ORDER.map((slug) =>
    products.find((p) => p.slug === slug),
  ).filter((p): p is Product => Boolean(p));

  const modules = products.filter((p) => p.category !== "platform-plans");
  const popularModules = modules.filter((p) => p.popular).slice(0, 3);
  const categoryName = (slug: string) =>
    categories.find((c) => c.slug === slug)?.name;

  const pricingFaq = getFaq()
    .filter((item) => item.group === "Pricing & Billing")
    .slice(0, 3);

  return (
    <>
      <PageHero
        eyebrow="Pricing"
        title="Pricing"
        description="Plans scale with analyzed call volume, not agent headcount. Every tier includes the full analysis pipeline — you pay for coverage, never per insight."
      />

      {/* ── Plans ── */}
      <section aria-labelledby="plans-heading" className="py-16 sm:py-24">
        <div className="container-site">
          <h2 id="plans-heading" className="sr-only">
            Platform plans
          </h2>
          <div className="grid items-start gap-6 lg:grid-cols-3 lg:gap-8 lg:py-4">
            {plans.map((plan) => (
              <PlanCard
                key={plan.slug}
                plan={plan}
                featured={plan.slug === "homayar-professional"}
              />
            ))}
          </div>
          <p className="mt-10 text-center text-sm text-ink-muted">
            Not sure which tier fits your call volume?{" "}
            <Link
              href="/contact"
              className="font-medium text-accent underline-offset-4 transition-colors hover:underline"
            >
              Talk to our team
            </Link>
          </p>
        </div>
      </section>

      {/* ── Modules ── */}
      <section
        aria-labelledby="modules-heading"
        className="border-t border-line bg-surface py-16 sm:py-24"
      >
        <div className="container-site">
          <SectionHeading
            eyebrow="Extend with modules"
            title="Add capability where your operation needs it"
            description={`Every plan can be extended with ${modules.length} specialized modules — from compliance monitoring to coaching workspaces and dashboard builders. Three of the most adopted:`}
          />
          <h2 id="modules-heading" className="sr-only">
            Add-on modules
          </h2>

          <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {popularModules.map((product) => (
              <ProductCard
                key={product.slug}
                product={product}
                categoryName={categoryName(product.category)}
              />
            ))}
          </div>

          <div className="mt-10 flex justify-center">
            <ButtonLink href="/catalog" variant="secondary" size="lg">
              Browse the full catalog
              <ArrowRightIcon width={18} height={18} />
            </ButtonLink>
          </div>
        </div>
      </section>

      {/* ── Billing notes ── */}
      <section
        aria-labelledby="billing-heading"
        className="border-t border-line py-16 sm:py-24"
      >
        <div className="container-site">
          <SectionHeading
            eyebrow="Billing"
            title="How billing works"
            description="Three billing types across the catalog, each shown on the product card before you add it to the cart."
          />
          <h2 id="billing-heading" className="sr-only">
            Billing notes
          </h2>

          <dl className="mt-10 grid gap-6 md:grid-cols-3">
            {BILLING_NOTES.map(({ term, note }) => (
              <div key={term} className="card-surface p-6">
                <dt className="font-mono text-sm font-semibold uppercase tracking-wider text-accent">
                  {term}
                </dt>
                <dd className="mt-3 text-sm leading-relaxed text-ink-soft">
                  {note}
                </dd>
              </div>
            ))}
          </dl>

          <p className="mt-8 rounded-xl border border-amber/30 bg-amber/10 px-5 py-4 text-sm leading-relaxed text-ink-soft">
            <span className="font-semibold text-amber">Test mode:</span>{" "}
            checkout on this site runs in Stripe test mode. No real payments
            are processed, and no card is ever charged.
          </p>
        </div>
      </section>

      {/* ── Pricing FAQ teaser ── */}
      <section
        aria-labelledby="pricing-faq-heading"
        className="border-t border-line bg-surface py-16 sm:py-24"
      >
        <div className="container-site">
          <SectionHeading
            eyebrow="FAQ"
            title="Pricing questions, answered plainly"
          />
          <h2 id="pricing-faq-heading" className="sr-only">
            Pricing and billing FAQ
          </h2>

          <div className="mt-10 space-y-4">
            {pricingFaq.map((item) => (
              <details key={item.question} className="card-surface group p-0">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-4 p-5 font-display text-base font-semibold text-ink [&::-webkit-details-marker]:hidden">
                  {item.question}
                  <ChevronDownIcon
                    width={18}
                    height={18}
                    className="shrink-0 text-ink-muted transition-transform group-open:rotate-180"
                  />
                </summary>
                <p className="border-t border-line px-5 py-4 text-sm leading-relaxed text-ink-soft">
                  {item.answer}
                </p>
              </details>
            ))}
          </div>

          <div className="mt-8">
            <ButtonLink href="/faq" variant="ghost">
              See all frequently asked questions
              <ArrowRightIcon width={16} height={16} />
            </ButtonLink>
          </div>
        </div>
      </section>
    </>
  );
}
