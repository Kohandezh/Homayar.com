"use client";

import Image from "next/image";
import Link from "next/link";
import { renderMarkdown } from "@/lib/markdown";
import type { BillingType, Category, Product } from "@/lib/types";
import { useLocale } from "@/lib/locale";
import {
  localizeCategories,
  localizeProduct,
  localizeProducts,
} from "@/lib/data-i18n";
import { badgeLabels, billingLabel, formatPriceL } from "@/lib/ui-i18n";
import { AddToCartButton } from "@/components/add-to-cart";
import { ProductCard } from "@/components/product-card";
import { Badge, ButtonLink, SectionHeading } from "@/components/ui";
import { ArrowRightIcon, CheckIcon } from "@/components/icons";

const BILLING_EXPLANATIONS: Record<BillingType, { fa: string; en: string }> = {
  monthly: {
    fa: "صورت‌حساب ماهانه، لغو در هر زمان",
    en: "Billed monthly, cancel anytime",
  },
  yearly: {
    fa: "صورت‌حساب سالانه، لغو در هر زمان",
    en: "Billed annually, cancel anytime",
  },
  "one-time": { fa: "خرید یک‌باره", en: "One-time purchase" },
  implementation: {
    fa: "پروژه پیاده‌سازی با دامنه مشخص",
    en: "Scoped implementation engagement",
  },
  custom: { fa: "سفارشی — با فروش گفت‌وگو کنید", en: "Custom — talk to sales" },
};

type Strings = {
  catalog: string;
  bookDemo: string;
  whoItsFor: string;
  overviewEyebrow: string;
  inside: (name: string) => string;
  impactEyebrow: string;
  keyOutcomes: string;
  keyOutcomesDesc: string;
  capabilitiesEyebrow: string;
  whatsIncluded: string;
  whatsIncludedDesc: (name: string) => string;
  keepExploring: string;
  relatedProducts: string;
  viewFullCatalog: string;
};

const STRINGS: Record<"fa" | "en", Strings> = {
  fa: {
    catalog: "کاتالوگ",
    bookDemo: "رزرو دمو",
    whoItsFor: "برای چه کسانی",
    overviewEyebrow: "نمای کلی",
    inside: (name) => `درون ${name}`,
    impactEyebrow: "تأثیر",
    keyOutcomes: "نتایج کلیدی",
    keyOutcomesDesc: "نتایج سنجش‌پذیری که تیم‌ها پس از به‌کارگیری این محصول می‌بینند.",
    capabilitiesEyebrow: "قابلیت‌ها",
    whatsIncluded: "آنچه شامل می‌شود",
    whatsIncludedDesc: (name) => `هر آنچه همراه ${name} عرضه می‌شود.`,
    keepExploring: "ادامه کاوش",
    relatedProducts: "محصولات مرتبط",
    viewFullCatalog: "مشاهده کاتالوگ کامل",
  },
  en: {
    catalog: "Catalog",
    bookDemo: "Book a demo",
    whoItsFor: "Who it's for",
    overviewEyebrow: "Overview",
    inside: (name) => `Inside ${name}`,
    impactEyebrow: "Impact",
    keyOutcomes: "Key outcomes",
    keyOutcomesDesc:
      "Measured results teams see after putting this product to work.",
    capabilitiesEyebrow: "Capabilities",
    whatsIncluded: "What's included",
    whatsIncludedDesc: (name) => `Everything that ships with ${name}.`,
    keepExploring: "Keep exploring",
    relatedProducts: "Related products",
    viewFullCatalog: "View full catalog",
  },
};

const STAT_RE =
  /^((?:Up to |Sub-|Top |~|تا |زیر |بالای )?[\d۰-۹][\d۰-۹,.،]*(?:\s?[-–]\s?[\d۰-۹][\d۰-۹,.،]*)?(?:%\+|%|٪|x|×|\+)?(?:\s?(?:hours?|minutes?|seconds?|days?|weeks?|ساعت|دقیقه|ثانیه|روز|هفته))?|Zero|Unlimited|صفر|نامحدود)\s+(.+)$/;

function splitOutcome(
  outcome: string,
  index: number,
): { stat: string; body: string } {
  const match = outcome.match(STAT_RE);
  if (match) return { stat: match[1], body: match[2] };
  return { stat: String(index + 1).padStart(2, "0"), body: outcome };
}

export function ProductDetail({
  product: rawProduct,
  categories: rawCategories,
  related: rawRelated,
}: {
  product: Product;
  categories: Category[];
  related: Product[];
}) {
  const { locale } = useLocale();
  const t = locale === "fa" ? STRINGS.fa : STRINGS.en;
  const badges = badgeLabels(locale);

  const product = localizeProduct(rawProduct, locale);
  const categories = localizeCategories(rawCategories, locale);
  const related = localizeProducts(rawRelated, locale);

  const category = categories.find((c) => c.slug === product.category);
  const categoryName = category?.name ?? product.category;
  const isCustom = product.billingType === "custom" || product.priceUsd === 0;
  void isCustom;

  return (
    <>
      <section className="border-b border-line">
        <div className="container-site py-10 sm:py-14">
          <nav aria-label="Breadcrumb" className="mb-8">
            <ol className="flex flex-wrap items-center gap-2 text-sm text-ink-muted">
              <li>
                <Link
                  href="/catalog"
                  className="transition-colors hover:text-accent"
                >
                  {t.catalog}
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
                {product.popular && <Badge tone="amber">{badges.popular}</Badge>}
                {product.recommended && (
                  <Badge tone="accent">{badges.recommended}</Badge>
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
                  {formatPriceL(product.priceUsd, product.billingType, locale)}
                </p>
                <p className="mt-1 text-sm text-ink-muted">
                  {locale === "fa"
                    ? BILLING_EXPLANATIONS[product.billingType].fa
                    : BILLING_EXPLANATIONS[product.billingType].en}
                </p>
              </div>

              <div className="mt-6 flex flex-col gap-3 sm:flex-row">
                <AddToCartButton product={product} size="lg" />
                <ButtonLink href="/contact" variant="secondary" size="lg">
                  {t.bookDemo}
                </ButtonLink>
              </div>

              <div className="mt-8 rounded-xl border border-line bg-surface p-5">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">
                  {t.whoItsFor}
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
          <SectionHeading
            eyebrow={t.overviewEyebrow}
            title={t.inside(product.name)}
          />
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
            eyebrow={t.impactEyebrow}
            title={t.keyOutcomes}
            description={t.keyOutcomesDesc}
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
            eyebrow={t.capabilitiesEyebrow}
            title={t.whatsIncluded}
            description={t.whatsIncludedDesc(product.name)}
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
              eyebrow={t.keepExploring}
              title={t.relatedProducts}
            />
            <Link
              href="/catalog"
              className="inline-flex items-center gap-2 text-sm font-semibold text-accent transition-colors hover:text-accent-strong"
            >
              {t.viewFullCatalog}
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
