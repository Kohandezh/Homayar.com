"use client";

import Link from "next/link";
import type { Category, Product } from "@/lib/types";
import { useLocale } from "@/lib/locale";
import { localizeCategories, localizeProducts } from "@/lib/data-i18n";
import { formatPriceL } from "@/lib/ui-i18n";
import { Badge, ButtonLink, SectionHeading } from "@/components/ui";
import { ArrowRightIcon, CheckIcon, ChevronDownIcon } from "@/components/icons";
import { AddToCartButton } from "@/components/add-to-cart";
import { ProductCard } from "@/components/product-card";
import { LocalizedHero } from "@/components/localized-hero";

const PLAN_ORDER = ["homayar-starter", "homayar-professional", "homayar-enterprise"];

type Qa = { question: string; answer: string };
type Strings = {
  hero: { eyebrow: string; title: string; description: string };
  plansHeading: string;
  mostPopular: string;
  customNote: string;
  monthlyNote: string;
  notSure: string;
  talkToTeam: string;
  modulesEyebrow: string;
  modulesTitle: string;
  modulesDesc: (count: number) => string;
  modulesHeading: string;
  browseCatalog: string;
  billingEyebrow: string;
  billingTitle: string;
  billingDesc: string;
  billingHeading: string;
  billingNotes: { term: string; note: string }[];
  testModeLabel: string;
  testModeBody: string;
  faqEyebrow: string;
  faqTitle: string;
  faqHeading: string;
  faq: Qa[];
  seeAllFaq: string;
};

const en: Strings = {
  hero: {
    eyebrow: "Pricing",
    title: "Pricing",
    description:
      "Plans scale with analyzed call volume, not agent headcount. Every tier includes the full analysis pipeline — you pay for coverage, never per insight.",
  },
  plansHeading: "Platform plans",
  mostPopular: "Most popular",
  customNote: "Volume-based annual contract, quoted in USD",
  monthlyNote: "Billed monthly, sized by analyzed call volume",
  notSure: "Not sure which tier fits your call volume?",
  talkToTeam: "Talk to our team",
  modulesEyebrow: "Extend with modules",
  modulesTitle: "Add capability where your operation needs it",
  modulesDesc: (count) =>
    `Every plan can be extended with ${count} specialized modules — from compliance monitoring to coaching workspaces and dashboard builders. Three of the most adopted:`,
  modulesHeading: "Add-on modules",
  browseCatalog: "Browse the full catalog",
  billingEyebrow: "Billing",
  billingTitle: "How billing works",
  billingDesc:
    "Three billing types across the catalog, each shown on the product card before you add it to the cart.",
  billingHeading: "Billing notes",
  billingNotes: [
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
  ],
  testModeLabel: "Test mode:",
  testModeBody:
    " checkout on this site runs in Stripe test mode. No real payments are processed, and no card is ever charged.",
  faqEyebrow: "FAQ",
  faqTitle: "Pricing questions, answered plainly",
  faqHeading: "Pricing and billing FAQ",
  faq: [
    {
      question: "How is Homayar priced?",
      answer:
        "Pricing is based on analyzed call volume — measured in audio hours per month — with plan tiers that bundle seats, retention, and integrations. There is no per-seat charge for agents whose calls are analyzed, so coverage never penalizes you for growing the team.",
    },
    {
      question: "Can we change plans mid-contract?",
      answer:
        "Upgrades take effect immediately, with the price difference prorated for the remainder of your billing period. Downgrades take effect at the next renewal so your historical data and configured workflows stay intact.",
    },
    {
      question: "What is your refund policy?",
      answer:
        "Monthly plans can be cancelled at any time and are not billed for subsequent months; we do not refund the current month once analysis has run. Annual contracts include a 30-day evaluation window with a prorated exit.",
    },
  ],
  seeAllFaq: "See all frequently asked questions",
};

const fa: Strings = {
  hero: {
    eyebrow: "تعرفه‌ها",
    title: "تعرفه‌ها",
    description:
      "طرح‌ها بر اساس حجم تماس‌های تحلیل‌شده مقیاس می‌گیرند، نه تعداد اپراتورها. هر سطح شامل خط پردازش کامل تحلیل است — شما برای پوشش پرداخت می‌کنید، نه به‌ازای هر بینش.",
  },
  plansHeading: "طرح‌های پلتفرم",
  mostPopular: "پرطرفدارترین",
  customNote: "قرارداد سالانه مبتنی بر حجم، با قیمت‌گذاری به دلار",
  monthlyNote: "صورت‌حساب ماهانه، متناسب با حجم تماس‌های تحلیل‌شده",
  notSure: "مطمئن نیستید کدام سطح با حجم تماس‌های شما مناسب است؟",
  talkToTeam: "با تیم ما گفت‌وگو کنید",
  modulesEyebrow: "گسترش با ماژول‌ها",
  modulesTitle: "قابلیت را دقیقاً جایی که عملیات شما نیاز دارد بیفزایید",
  modulesDesc: (count) =>
    `هر طرح را می‌توان با ${count} ماژول تخصصی گسترش داد — از پایش انطباق تا فضای کاری مربی‌گری و سازنده‌های داشبورد. سه مورد از پرکاربردترین‌ها:`,
  modulesHeading: "ماژول‌های افزودنی",
  browseCatalog: "مرور کاتالوگ کامل",
  billingEyebrow: "صورت‌حساب",
  billingTitle: "صورت‌حساب چگونه کار می‌کند",
  billingDesc:
    "سه نوع صورت‌حساب در سراسر کاتالوگ، که هر کدام پیش از افزودن به سبد روی کارت محصول نمایش داده می‌شود.",
  billingHeading: "یادداشت‌های صورت‌حساب",
  billingNotes: [
    {
      term: "ماهانه",
      note: "طرح‌های پلتفرم و بیشتر ماژول‌ها به‌صورت ماهانه و متناسب با حجم تماس‌های تحلیل‌شده صورت‌حساب می‌شوند. هر زمان ارتقا دهید؛ پیش از چرخه بعدی بدون جریمه لغو کنید.",
    },
    {
      term: "سالانه",
      note: "اقلام سالانه — برنامه‌های آموزشی و خدمات مدیریت‌شده — یک‌بار در سال صورت‌حساب می‌شوند و شامل یک بازه ارزیابی ۳۰ روزه با خروج به‌نسبت زمان است.",
    },
    {
      term: "تک‌خرید",
      note: "بسته‌های پیاده‌سازی و خدمات با دامنه ثابت، یک‌بار صورت‌حساب می‌شوند و دامنه کار پیش از آغاز به‌صورت کتبی توافق می‌شود.",
    },
  ],
  testModeLabel: "حالت آزمایشی:",
  testModeBody:
    " تسویه‌حساب در این سایت در حالت آزمایشی Stripe اجرا می‌شود. هیچ پرداخت واقعی پردازش نمی‌شود و هیچ کارتی شارژ نمی‌گردد.",
  faqEyebrow: "پرسش‌های متداول",
  faqTitle: "پرسش‌های تعرفه، با پاسخ‌های روشن",
  faqHeading: "پرسش‌های متداول تعرفه و صورت‌حساب",
  faq: [
    {
      question: "تعرفه همایار چگونه تعیین می‌شود؟",
      answer:
        "قیمت‌گذاری بر اساس حجم تماس‌های تحلیل‌شده — اندازه‌گیری‌شده برحسب ساعت صوت در ماه — با سطوحی است که صندلی‌ها، نگهداری داده و یکپارچه‌سازی‌ها را بسته‌بندی می‌کنند. برای اپراتورهایی که تماس‌هایشان تحلیل می‌شود هیچ هزینه به‌ازای هر صندلی وجود ندارد، بنابراین پوشش هرگز شما را بابت بزرگ‌تر شدن تیم جریمه نمی‌کند.",
    },
    {
      question: "آیا می‌توانیم در میانه قرارداد طرح را تغییر دهیم؟",
      answer:
        "ارتقاها بی‌درنگ اعمال می‌شوند و تفاوت قیمت برای باقی‌مانده دوره صورت‌حساب به‌نسبت محاسبه می‌شود. تنزل‌ها در تمدید بعدی اعمال می‌شوند تا داده‌های تاریخی و گردش‌کارهای پیکربندی‌شده شما دست‌نخورده بمانند.",
    },
    {
      question: "سیاست بازگشت وجه شما چیست؟",
      answer:
        "طرح‌های ماهانه را می‌توان در هر زمان لغو کرد و برای ماه‌های بعد صورت‌حساب نمی‌شوند؛ پس از اجرای تحلیل، وجه ماه جاری بازگردانده نمی‌شود. قراردادهای سالانه شامل یک بازه ارزیابی ۳۰ روزه با خروج به‌نسبت زمان هستند.",
    },
  ],
  seeAllFaq: "مشاهده همه پرسش‌های متداول",
};

function PlanCard({
  plan,
  featured,
  t,
  locale,
}: {
  plan: Product;
  featured: boolean;
  t: Strings;
  locale: "fa" | "en";
}) {
  return (
    <article
      className={`card-surface relative flex flex-col p-6 sm:p-8 ${
        featured ? "glow-accent border-accent/40 lg:-my-4 lg:py-12" : ""
      }`}
    >
      {featured && (
        <div className="absolute -top-3 left-1/2 -translate-x-1/2">
          <Badge tone="accent">{t.mostPopular}</Badge>
        </div>
      )}
      <h3 className="font-display text-xl font-semibold tracking-tight text-ink">
        {plan.name}
      </h3>
      <p className="mt-1.5 text-sm text-ink-muted">{plan.tagline}</p>
      <p className="mt-5 font-mono text-3xl font-semibold text-ink sm:text-4xl">
        {formatPriceL(plan.priceUsd, plan.billingType, locale)}
      </p>
      <p className="mt-1 text-xs text-ink-muted">
        {plan.billingType === "custom" ? t.customNote : t.monthlyNote}
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

export function PricingContent({
  products: rawProducts,
  categories: rawCategories,
}: {
  products: Product[];
  categories: Category[];
}) {
  const { locale } = useLocale();
  const loc = locale === "fa" ? "fa" : "en";
  const t = loc === "fa" ? fa : en;

  const products = localizeProducts(rawProducts, locale);
  const categories = localizeCategories(rawCategories, locale);

  const plans = PLAN_ORDER.map((slug) =>
    products.find((p) => p.slug === slug),
  ).filter((p): p is Product => Boolean(p));

  const modules = products.filter((p) => p.category !== "platform-plans");
  const popularModules = modules.filter((p) => p.popular).slice(0, 3);
  const categoryName = (slug: string) =>
    categories.find((c) => c.slug === slug)?.name;

  return (
    <>
      <LocalizedHero copy={{ fa: fa.hero, en: en.hero }} />

      {/* ── Plans ── */}
      <section aria-labelledby="plans-heading" className="py-16 sm:py-24">
        <div className="container-site">
          <h2 id="plans-heading" className="sr-only">
            {t.plansHeading}
          </h2>
          <div className="grid items-start gap-6 lg:grid-cols-3 lg:gap-8 lg:py-4">
            {plans.map((plan) => (
              <PlanCard
                key={plan.slug}
                plan={plan}
                featured={plan.slug === "homayar-professional"}
                t={t}
                locale={loc}
              />
            ))}
          </div>
          <p className="mt-10 text-center text-sm text-ink-muted">
            {t.notSure}{" "}
            <Link
              href="/contact"
              className="font-medium text-accent underline-offset-4 transition-colors hover:underline"
            >
              {t.talkToTeam}
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
            eyebrow={t.modulesEyebrow}
            title={t.modulesTitle}
            description={t.modulesDesc(modules.length)}
          />
          <h2 id="modules-heading" className="sr-only">
            {t.modulesHeading}
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
              {t.browseCatalog}
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
            eyebrow={t.billingEyebrow}
            title={t.billingTitle}
            description={t.billingDesc}
          />
          <h2 id="billing-heading" className="sr-only">
            {t.billingHeading}
          </h2>

          <dl className="mt-10 grid gap-6 md:grid-cols-3">
            {t.billingNotes.map(({ term, note }) => (
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
            <span className="font-semibold text-amber">{t.testModeLabel}</span>
            {t.testModeBody}
          </p>
        </div>
      </section>

      {/* ── Pricing FAQ teaser ── */}
      <section
        aria-labelledby="pricing-faq-heading"
        className="border-t border-line bg-surface py-16 sm:py-24"
      >
        <div className="container-site">
          <SectionHeading eyebrow={t.faqEyebrow} title={t.faqTitle} />
          <h2 id="pricing-faq-heading" className="sr-only">
            {t.faqHeading}
          </h2>

          <div className="mt-10 space-y-4">
            {t.faq.map((item) => (
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
              {t.seeAllFaq}
              <ArrowRightIcon width={16} height={16} />
            </ButtonLink>
          </div>
        </div>
      </section>
    </>
  );
}
