import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getCategories, getProductBySlug, getProducts } from "@/lib/catalog";
import { renderMarkdown } from "@/lib/markdown";
import { absoluteUrl } from "@/lib/site";
import type { BillingType, Product } from "@/lib/types";
import { formatPrice } from "@/lib/types";
import { AddToCartButton } from "@/components/add-to-cart";
import { ProductCard } from "@/components/product-card";
import { Badge, ButtonLink, SectionHeading } from "@/components/ui";
import { ArrowRightIcon, CheckIcon } from "@/components/icons";

const BILLING_EXPLANATIONS: Record<BillingType, string> = {
  monthly: "Billed monthly, cancel anytime",
  yearly: "Billed annually, cancel anytime",
  "one-time": "One-time purchase",
  implementation: "Scoped implementation engagement",
  custom: "Custom — talk to sales",
};

/**
 * Pulls a leading metric ("100%", "Up to 70%", "Sub-2x", "Top 10", "Zero",
 * "Unlimited") out of an outcome sentence for stat-style display. Falls back
 * to a zero-padded index when the outcome has no leading figure.
 */
const STAT_RE =
  /^((?:Up to |Sub-|Top |~)?\d[\d,.]*(?:\s?[-–]\s?\d[\d,.]*)?(?:%\+|%|x|\+)?(?:\s?(?:hours?|minutes?|seconds?|days?|weeks?))?|Zero|Unlimited)\s+(.+)$/;

function splitOutcome(
  outcome: string,
  index: number,
): { stat: string; body: string } {
  const match = outcome.match(STAT_RE);
  if (match) return { stat: match[1], body: match[2] };
  return { stat: String(index + 1).padStart(2, "0"), body: outcome };
}

function relatedProducts(all: Product[], current: Product): Product[] {
  const sameCategory = all.filter(
    (p) => p.category === current.category && p.slug !== current.slug,
  );
  const picks = sameCategory.slice(0, 3);
  if (picks.length < 3) {
    const chosen = new Set(picks.map((p) => p.slug));
    const fallback = all
      .filter((p) => p.slug !== current.slug && !chosen.has(p.slug))
      .sort((a, b) => Number(b.popular) - Number(a.popular));
    for (const p of fallback) {
      if (picks.length >= 3) break;
      picks.push(p);
    }
  }
  return picks;
}

export async function generateStaticParams() {
  const products = await getProducts();
  return products.map((p) => ({ slug: p.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const product = await getProductBySlug(slug);
  if (!product) return {};
  return {
    title: product.seoTitle,
    description: product.seoDescription,
    keywords: product.seoKeywords,
    alternates: { canonical: absoluteUrl(`/catalog/${product.slug}`) },
    openGraph: {
      title: product.seoTitle,
      description: product.seoDescription,
      images: [{ url: product.image, alt: `${product.name} artwork` }],
    },
  };
}

export default async function ProductDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const [product, categories, allProducts] = await Promise.all([
    getProductBySlug(slug),
    getCategories(),
    getProducts(),
  ]);
  if (!product) notFound();

  const category = categories.find((c) => c.slug === product.category);
  const categoryName = category?.name ?? product.category;
  const related = relatedProducts(allProducts, product);
  const isCustom = product.billingType === "custom" || product.priceUsd === 0;

  const jsonLd: Record<string, unknown> = {
    "@context": "https://schema.org",
    "@type": "Product",
    name: product.name,
    description: product.seoDescription,
    image: absoluteUrl(product.image),
    brand: { "@type": "Brand", name: "Homayar" },
  };
  if (!isCustom) {
    jsonLd.offers = {
      "@type": "Offer",
      url: absoluteUrl(`/catalog/${product.slug}`),
      price: product.priceUsd,
      priceCurrency: "USD",
      availability: "https://schema.org/InStock",
    };
  }

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <section className="border-b border-line">
        <div className="container-site py-10 sm:py-14">
          <nav aria-label="Breadcrumb" className="mb-8">
            <ol className="flex flex-wrap items-center gap-2 text-sm text-ink-muted">
              <li>
                <Link
                  href="/catalog"
                  className="transition-colors hover:text-accent"
                >
                  Catalog
                </Link>
              </li>
              <li aria-hidden className="text-ink-faint">
                /
              </li>
              <li>
                <Link
                  href={`/catalog?category=${product.category}`}
                  className="transition-colors hover:text-accent"
                >
                  {categoryName}
                </Link>
              </li>
              <li aria-hidden className="text-ink-faint">
                /
              </li>
              <li>
                <span aria-current="page" className="text-ink">
                  {product.name}
                </span>
              </li>
            </ol>
          </nav>

          <div className="grid gap-10 lg:grid-cols-2 lg:gap-14">
            <div className="card-surface relative aspect-square overflow-hidden">
              <Image
                src={product.image}
                alt={`${product.name} artwork`}
                fill
                priority
                sizes="(max-width: 1024px) 100vw, 50vw"
                className="object-cover"
              />
            </div>

            <div className="flex flex-col justify-center">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="neutral">{categoryName}</Badge>
                {product.popular && <Badge tone="amber">Popular</Badge>}
                {product.recommended && (
                  <Badge tone="accent">Recommended</Badge>
                )}
              </div>

              <h1 className="mt-4 font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
                {product.name}
              </h1>
              <p className="mt-2 text-lg text-accent">{product.tagline}</p>
              <p className="mt-4 leading-relaxed text-ink-soft">
                {product.shortDescription}
              </p>

              <div className="mt-6">
                <p className="font-mono text-3xl font-semibold text-ink">
                  {formatPrice(product.priceUsd, product.billingType)}
                </p>
                <p className="mt-1 text-sm text-ink-muted">
                  {BILLING_EXPLANATIONS[product.billingType]}
                </p>
              </div>

              <div className="mt-6 flex flex-col gap-3 sm:flex-row">
                <AddToCartButton product={product} size="lg" />
                <ButtonLink href="/contact" variant="secondary" size="lg">
                  Book a demo
                </ButtonLink>
              </div>

              <div className="mt-8 rounded-xl border border-line bg-surface p-5">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">
                  Who it&apos;s for
                </p>
                <p className="mt-2 text-sm leading-relaxed text-ink-soft">
                  {product.whoItsFor}
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="border-b border-line">
        <div className="container-site py-14 sm:py-20">
          <SectionHeading eyebrow="Overview" title={`Inside ${product.name}`} />
          <div
            className="prose-dark mt-8 max-w-3xl"
            dangerouslySetInnerHTML={{
              __html: renderMarkdown(product.longDescription),
            }}
          />
        </div>
      </section>

      <section className="border-b border-line">
        <div className="container-site py-14 sm:py-20">
          <SectionHeading
            eyebrow="Impact"
            title="Key outcomes"
            description="Measured results teams see after putting this product to work."
          />
          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {product.keyOutcomes.slice(0, 5).map((outcome, i) => {
              const { stat, body } = splitOutcome(outcome, i);
              return (
                <div key={outcome} className="card-surface p-5">
                  <p className="font-mono text-2xl font-semibold text-accent">
                    {stat}
                  </p>
                  <p className="mt-2 text-sm leading-relaxed text-ink-soft">
                    {body}
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      <section className="border-b border-line">
        <div className="container-site py-14 sm:py-20">
          <SectionHeading
            eyebrow="Capabilities"
            title="What's included"
            description={`Everything that ships with ${product.name}.`}
          />
          <ul className="mt-10 grid gap-x-8 gap-y-4 sm:grid-cols-2">
            {product.features.map((feature) => (
              <li key={feature} className="flex items-start gap-3">
                <CheckIcon
                  aria-hidden
                  width={18}
                  height={18}
                  className="mt-0.5 shrink-0 text-accent"
                />
                <span className="text-sm leading-relaxed text-ink-soft">
                  {feature}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section>
        <div className="container-site py-14 sm:py-20">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <SectionHeading
              eyebrow="Keep exploring"
              title="Related products"
            />
            <Link
              href="/catalog"
              className="inline-flex items-center gap-2 text-sm font-semibold text-accent transition-colors hover:text-accent-strong"
            >
              View full catalog
              <ArrowRightIcon aria-hidden width={16} height={16} />
            </Link>
          </div>
          <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {related.map((p) => (
              <ProductCard
                key={p.slug}
                product={p}
                categoryName={
                  categories.find((c) => c.slug === p.category)?.name
                }
              />
            ))}
          </div>
        </div>
      </section>
    </>
  );
}
