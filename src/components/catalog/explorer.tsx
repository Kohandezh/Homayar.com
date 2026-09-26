"use client";

import { useMemo, useState, type ReactNode } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type {
  BillingType,
  Category,
  Industry,
  Product,
  TeamSize,
} from "@/lib/types";
import { ProductCard } from "@/components/product-card";
import { CategoryIcon, ChevronDownIcon, CloseIcon } from "@/components/icons";
import { useLocale } from "@/lib/locale";
import type { Locale } from "@/lib/content";
import { localizeCategories, localizeProducts } from "@/lib/data-i18n";

/* ── Filter vocab (values are stable URL params; labels are localized) ── */

const PRICE_VALUES = ["under-300", "300-800", "over-800", "custom"] as const;
type PriceRange = (typeof PRICE_VALUES)[number];

const BILLING_VALUES: BillingType[] = [
  "monthly",
  "yearly",
  "one-time",
  "implementation",
  "custom",
];

const TEAM_VALUES: TeamSize[] = ["startup", "smb", "mid-market", "enterprise"];

const INDUSTRY_VALUES: Industry[] = [
  "banking",
  "healthcare",
  "insurance",
  "telecom",
  "retail",
  "government",
  "technology",
];

const SORT_VALUES = ["featured", "price-asc", "price-desc", "name"] as const;
type SortKey = (typeof SORT_VALUES)[number];

/* ── Localized filter strings ───────────────────────────────────── */

type ExplorerStrings = {
  filters: string;
  category: string;
  all: string;
  priceRange: string;
  anyPrice: string;
  billingType: string;
  teamSize: string;
  industry: string;
  allIndustries: string;
  highlights: string;
  popular: string;
  recommended: string;
  clearAllFilters: string;
  clearAll: string;
  sortBy: string;
  showing: (shown: number, total: number) => string;
  noMatchTitle: string;
  noMatchBody: string;
  price: Record<PriceRange, string>;
  billing: Record<BillingType, string>;
  team: Record<TeamSize, string>;
  industries: Record<Industry, string>;
  sort: Record<SortKey, string>;
};

const en: ExplorerStrings = {
  filters: "Filters",
  category: "Category",
  all: "All",
  priceRange: "Price range",
  anyPrice: "Any price",
  billingType: "Billing type",
  teamSize: "Team size",
  industry: "Industry",
  allIndustries: "All industries",
  highlights: "Highlights",
  popular: "Popular",
  recommended: "Recommended",
  clearAllFilters: "Clear all filters",
  clearAll: "Clear all",
  sortBy: "Sort by",
  showing: (shown, total) => `Showing ${shown} of ${total} products`,
  noMatchTitle: "No products match these filters",
  noMatchBody:
    "Try removing a filter or two — every Homayar module, plan, and service is one click away.",
  price: {
    "under-300": "Under $300",
    "300-800": "$300 – $800",
    "over-800": "Over $800",
    custom: "Custom pricing",
  },
  billing: {
    monthly: "Monthly",
    yearly: "Yearly",
    "one-time": "One-time",
    implementation: "Implementation",
    custom: "Custom",
  },
  team: {
    startup: "Startup",
    smb: "SMB",
    "mid-market": "Mid-market",
    enterprise: "Enterprise",
  },
  industries: {
    banking: "Banking",
    healthcare: "Healthcare",
    insurance: "Insurance",
    telecom: "Telecom",
    retail: "Retail",
    government: "Government",
    technology: "Technology",
  },
  sort: {
    featured: "Featured",
    "price-asc": "Price: low to high",
    "price-desc": "Price: high to low",
    name: "Name A–Z",
  },
};

const fa: ExplorerStrings = {
  filters: "فیلترها",
  category: "دسته‌بندی",
  all: "همه",
  priceRange: "بازه قیمت",
  anyPrice: "هر قیمتی",
  billingType: "نوع صورت‌حساب",
  teamSize: "اندازه تیم",
  industry: "صنعت",
  allIndustries: "همه صنایع",
  highlights: "برجسته‌ها",
  popular: "پرطرفدار",
  recommended: "پیشنهادی",
  clearAllFilters: "پاک کردن همه فیلترها",
  clearAll: "پاک کردن همه",
  sortBy: "مرتب‌سازی بر اساس",
  showing: (shown, total) => `نمایش ${shown} از ${total} محصول`,
  noMatchTitle: "هیچ محصولی با این فیلترها مطابقت ندارد",
  noMatchBody:
    "یک یا دو فیلتر را بردارید — هر ماژول، طرح و خدمت همایار تنها یک کلیک فاصله دارد.",
  price: {
    "under-300": "زیر ۳۰۰ دلار",
    "300-800": "۳۰۰ تا ۸۰۰ دلار",
    "over-800": "بالای ۸۰۰ دلار",
    custom: "قیمت سفارشی",
  },
  billing: {
    monthly: "ماهانه",
    yearly: "سالانه",
    "one-time": "تک‌خرید",
    implementation: "پیاده‌سازی",
    custom: "سفارشی",
  },
  team: {
    startup: "استارتاپ",
    smb: "کسب‌وکار کوچک",
    "mid-market": "متوسط",
    enterprise: "سازمانی",
  },
  industries: {
    banking: "بانکداری",
    healthcare: "سلامت",
    insurance: "بیمه",
    telecom: "مخابرات",
    retail: "خرده‌فروشی",
    government: "دولتی",
    technology: "فناوری",
  },
  sort: {
    featured: "ویژه",
    "price-asc": "قیمت: کم به زیاد",
    "price-desc": "قیمت: زیاد به کم",
    name: "نام: الفبایی",
  },
};

const STRINGS: Record<Locale, ExplorerStrings> = {
  fa,
  en,
  de: en,
  ar: en,
  fr: en,
  es: en,
};

/* ── Param parsing helpers ──────────────────────────────────────── */

function parseList<T extends string>(
  raw: string | null,
  valid: readonly T[],
): T[] {
  if (!raw) return [];
  const set = new Set<string>(valid);
  return raw.split(",").filter((v): v is T => set.has(v));
}

function parseOne<T extends string>(
  raw: string | null,
  valid: readonly T[],
): T | "" {
  if (raw && (valid as readonly string[]).includes(raw)) return raw as T;
  return "";
}

const isCustomPriced = (p: Product) =>
  p.billingType === "custom" || p.priceUsd === 0;

function matchesPrice(p: Product, range: PriceRange): boolean {
  switch (range) {
    case "under-300":
      return !isCustomPriced(p) && p.priceUsd < 300;
    case "300-800":
      return !isCustomPriced(p) && p.priceUsd >= 300 && p.priceUsd <= 800;
    case "over-800":
      return !isCustomPriced(p) && p.priceUsd > 800;
    case "custom":
      return isCustomPriced(p);
  }
}

function featuredScore(p: Product): number {
  return (p.recommended ? 2 : 0) + (p.popular ? 1 : 0);
}

/* ── Small UI primitives ────────────────────────────────────────── */

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
        active
          ? "border-accent/60 bg-accent/10 text-accent"
          : "border-line bg-surface text-ink-soft hover:border-line-strong hover:text-ink"
      }`}
    >
      {children}
    </button>
  );
}

function FilterGroup({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <fieldset className="border-0 p-0">
      <legend className="mb-2.5 text-xs font-semibold uppercase tracking-wider text-ink-muted">
        {label}
      </legend>
      {children}
    </fieldset>
  );
}

const SELECT_CLASSES =
  "w-full appearance-none rounded-lg border border-line bg-surface px-3 py-2 pr-9 text-sm text-ink transition-colors hover:border-line-strong focus:border-accent focus:outline-none";

function SelectWrap({ children }: { children: ReactNode }) {
  return (
    <div className="relative">
      {children}
      <ChevronDownIcon
        width={16}
        height={16}
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-ink-muted"
      />
    </div>
  );
}

/* ── Explorer ───────────────────────────────────────────────────── */

export function CatalogExplorer({
  products: rawProducts,
  categories: rawCategories,
}: {
  products: Product[];
  categories: Category[];
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { locale } = useLocale();
  const [mobileOpen, setMobileOpen] = useState(false);

  const t = STRINGS[locale] ?? en;
  const products = useMemo(
    () => localizeProducts(rawProducts, locale),
    [rawProducts, locale],
  );
  const categories = useMemo(
    () => localizeCategories(rawCategories, locale),
    [rawCategories, locale],
  );

  const categorySlugs = useMemo(
    () => categories.map((c) => c.slug),
    [categories],
  );
  const categoryNames = useMemo(
    () => new Map(categories.map((c) => [c.slug, c.name])),
    [categories],
  );

  /* All filter state lives in the URL — shareable and back-button safe. */
  const category = parseOne(searchParams.get("category"), categorySlugs);
  const price = parseOne(searchParams.get("price"), PRICE_VALUES);
  const billing = parseList(searchParams.get("billing"), BILLING_VALUES);
  const team = parseList(searchParams.get("team"), TEAM_VALUES);
  const industry = parseOne(searchParams.get("industry"), INDUSTRY_VALUES);
  const popular = searchParams.get("popular") === "1";
  const recommended = searchParams.get("recommended") === "1";
  const sort: SortKey =
    parseOne(searchParams.get("sort"), SORT_VALUES) || "featured";

  function setParams(updates: Record<string, string | null>) {
    const next = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value === null || value === "") next.delete(key);
      else next.set(key, value);
    }
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }

  function toggleListValue(key: "billing" | "team", value: string) {
    const current = key === "billing" ? billing : team;
    const next = (current as string[]).includes(value)
      ? (current as string[]).filter((v) => v !== value)
      : [...current, value];
    setParams({ [key]: next.length > 0 ? next.join(",") : null });
  }

  function clearAll() {
    router.replace(pathname, { scroll: false });
  }

  /* ── Filtering + sorting ── */
  // Plain computations: billing and team are new arrays on every render, so a
  // memo keyed on them never held, and the product list is small.

  const filtered = products.filter((p) => {
    if (category && p.category !== category) return false;
    if (price && !matchesPrice(p, price)) return false;
    if (billing.length > 0 && !billing.includes(p.billingType)) return false;
    if (team.length > 0 && !team.some((t) => p.teamSize.includes(t)))
      return false;
    if (industry && !p.industries.includes(industry)) return false;
    if (popular && !p.popular) return false;
    if (recommended && !p.recommended) return false;
    return true;
  });

  const sorted = [...filtered];
  if (sort === "price-asc" || sort === "price-desc") {
    const eff = (p: Product) =>
      isCustomPriced(p) ? Number.POSITIVE_INFINITY : p.priceUsd;
    sorted.sort((a, b) => {
      const ea = eff(a);
      const eb = eff(b);
      if (ea === eb) return 0;
      /* Custom-priced items always sink to the end of price sorts. */
      if (ea === Number.POSITIVE_INFINITY) return 1;
      if (eb === Number.POSITIVE_INFINITY) return -1;
      return sort === "price-asc" ? ea - eb : eb - ea;
    });
  } else if (sort === "name") {
    sorted.sort((a, b) => a.name.localeCompare(b.name));
  } else {
    sorted.sort((a, b) => featuredScore(b) - featuredScore(a));
  }

  /* ── Active-filter pills ── */

  const pills: { key: string; label: string; onRemove: () => void }[] = [];
  if (category) {
    pills.push({
      key: `category-${category}`,
      label: categoryNames.get(category) ?? category,
      onRemove: () => setParams({ category: null }),
    });
  }
  if (price) {
    pills.push({
      key: `price-${price}`,
      label: t.price[price],
      onRemove: () => setParams({ price: null }),
    });
  }
  for (const b of billing) {
    pills.push({
      key: `billing-${b}`,
      label: t.billing[b],
      onRemove: () => toggleListValue("billing", b),
    });
  }
  for (const tm of team) {
    pills.push({
      key: `team-${tm}`,
      label: t.team[tm],
      onRemove: () => toggleListValue("team", tm),
    });
  }
  if (industry) {
    pills.push({
      key: `industry-${industry}`,
      label: t.industries[industry],
      onRemove: () => setParams({ industry: null }),
    });
  }
  if (popular) {
    pills.push({
      key: "popular",
      label: t.popular,
      onRemove: () => setParams({ popular: null }),
    });
  }
  if (recommended) {
    pills.push({
      key: "recommended",
      label: t.recommended,
      onRemove: () => setParams({ recommended: null }),
    });
  }

  const activeCount = pills.length;

  /* ── Filter panel (rendered twice: mobile collapsible + lg sidebar) ── */

  const renderFilters = (idPrefix: string) => (
    <div className="space-y-7">
      <FilterGroup label={t.category}>
        <div className="flex flex-wrap gap-2">
          <Chip active={!category} onClick={() => setParams({ category: null })}>
            {t.all}
          </Chip>
          {categories.map((c) => (
            <Chip
              key={c.slug}
              active={category === c.slug}
              onClick={() =>
                setParams({ category: category === c.slug ? null : c.slug })
              }
            >
              <CategoryIcon icon={c.icon} width={15} height={15} />
              {c.name}
            </Chip>
          ))}
        </div>
      </FilterGroup>

      <div>
        <label
          htmlFor={`${idPrefix}-price`}
          className="mb-2.5 block text-xs font-semibold uppercase tracking-wider text-ink-muted"
        >
          {t.priceRange}
        </label>
        <SelectWrap>
          <select
            id={`${idPrefix}-price`}
            value={price}
            onChange={(e) => setParams({ price: e.target.value || null })}
            className={SELECT_CLASSES}
          >
            <option value="">{t.anyPrice}</option>
            {PRICE_VALUES.map((value) => (
              <option key={value} value={value}>
                {t.price[value]}
              </option>
            ))}
          </select>
        </SelectWrap>
      </div>

      <FilterGroup label={t.billingType}>
        <div className="flex flex-wrap gap-2">
          {BILLING_VALUES.map((value) => (
            <Chip
              key={value}
              active={billing.includes(value)}
              onClick={() => toggleListValue("billing", value)}
            >
              {t.billing[value]}
            </Chip>
          ))}
        </div>
      </FilterGroup>

      <FilterGroup label={t.teamSize}>
        <div className="flex flex-wrap gap-2">
          {TEAM_VALUES.map((value) => (
            <Chip
              key={value}
              active={team.includes(value)}
              onClick={() => toggleListValue("team", value)}
            >
              {t.team[value]}
            </Chip>
          ))}
        </div>
      </FilterGroup>

      <div>
        <label
          htmlFor={`${idPrefix}-industry`}
          className="mb-2.5 block text-xs font-semibold uppercase tracking-wider text-ink-muted"
        >
          {t.industry}
        </label>
        <SelectWrap>
          <select
            id={`${idPrefix}-industry`}
            value={industry}
            onChange={(e) => setParams({ industry: e.target.value || null })}
            className={SELECT_CLASSES}
          >
            <option value="">{t.allIndustries}</option>
            {INDUSTRY_VALUES.map((value) => (
              <option key={value} value={value}>
                {t.industries[value]}
              </option>
            ))}
          </select>
        </SelectWrap>
      </div>

      <FilterGroup label={t.highlights}>
        <div className="flex flex-wrap gap-2">
          <Chip
            active={popular}
            onClick={() => setParams({ popular: popular ? null : "1" })}
          >
            {t.popular}
          </Chip>
          <Chip
            active={recommended}
            onClick={() =>
              setParams({ recommended: recommended ? null : "1" })
            }
          >
            {t.recommended}
          </Chip>
        </div>
      </FilterGroup>

      {activeCount > 0 && (
        <button
          type="button"
          onClick={clearAll}
          className="text-sm font-medium text-ink-muted underline underline-offset-4 transition-colors hover:text-accent"
        >
          {t.clearAllFilters}
        </button>
      )}
    </div>
  );

  return (
    <div className="lg:grid lg:grid-cols-[280px_1fr] lg:gap-10 xl:grid-cols-[300px_1fr]">
      {/* Mobile filter toggle */}
      <div className="lg:hidden">
        <button
          type="button"
          onClick={() => setMobileOpen((open) => !open)}
          aria-expanded={mobileOpen}
          aria-controls="catalog-mobile-filters"
          className="inline-flex w-full items-center justify-between rounded-xl border border-line bg-surface px-4 py-3 text-sm font-semibold text-ink transition-colors hover:border-line-strong"
        >
          <span>
            {t.filters}
            {activeCount > 0 && (
              <span className="ml-2 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-accent px-1.5 font-mono text-xs font-bold text-bg">
                {activeCount}
              </span>
            )}
          </span>
          <ChevronDownIcon
            width={18}
            height={18}
            className={`text-ink-muted transition-transform ${
              mobileOpen ? "rotate-180" : ""
            }`}
          />
        </button>
        {mobileOpen && (
          <div
            id="catalog-mobile-filters"
            className="card-surface mt-3 p-5"
          >
            {renderFilters("m")}
          </div>
        )}
      </div>

      {/* Desktop sidebar */}
      <aside
        aria-label="Catalog filters"
        className="hidden lg:block"
      >
        <div className="sticky top-20 max-h-[calc(100vh-6rem)] overflow-y-auto pb-8 pr-1">
          {renderFilters("d")}
        </div>
      </aside>

      {/* Results */}
      <div className="mt-8 lg:mt-0">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <p aria-live="polite" className="text-sm text-ink-soft">
            {t.showing(sorted.length, products.length)}
          </p>
          <div className="flex items-center gap-2.5">
            <label
              htmlFor="catalog-sort"
              className="shrink-0 text-sm text-ink-muted"
            >
              {t.sortBy}
            </label>
            <SelectWrap>
              <select
                id="catalog-sort"
                value={sort}
                onChange={(e) =>
                  setParams({
                    sort: e.target.value === "featured" ? null : e.target.value,
                  })
                }
                className={SELECT_CLASSES}
              >
                {SORT_VALUES.map((value) => (
                  <option key={value} value={value}>
                    {t.sort[value]}
                  </option>
                ))}
              </select>
            </SelectWrap>
          </div>
        </div>

        {/* Active filter pills */}
        {activeCount > 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            {pills.map((pill) => (
              <span
                key={pill.key}
                className="inline-flex items-center gap-1.5 rounded-full border border-accent/40 bg-accent/10 py-1 pl-3 pr-1.5 text-xs font-medium text-accent"
              >
                {pill.label}
                <button
                  type="button"
                  onClick={pill.onRemove}
                  aria-label={`Remove ${pill.label} filter`}
                  className="rounded-full p-0.5 transition-colors hover:bg-accent/20 hover:text-ink"
                >
                  <CloseIcon width={12} height={12} />
                </button>
              </span>
            ))}
            <button
              type="button"
              onClick={clearAll}
              className="ml-1 text-xs font-medium text-ink-muted underline underline-offset-4 transition-colors hover:text-accent"
            >
              {t.clearAll}
            </button>
          </div>
        )}

        {sorted.length > 0 ? (
          <div className="mt-6 grid grid-cols-1 gap-6 sm:grid-cols-2 xl:grid-cols-3">
            {sorted.map((product) => (
              <ProductCard
                key={product.slug}
                product={product}
                categoryName={categoryNames.get(product.category)}
              />
            ))}
          </div>
        ) : (
          <div className="card-surface mt-6 flex flex-col items-center gap-4 p-12 text-center">
            <p className="font-display text-xl font-semibold tracking-tight text-ink">
              {t.noMatchTitle}
            </p>
            <p className="max-w-md text-sm leading-relaxed text-ink-muted">
              {t.noMatchBody}
            </p>
            <button
              type="button"
              onClick={clearAll}
              className="inline-flex h-10 items-center justify-center rounded-lg bg-accent px-4 text-sm font-semibold text-bg transition-colors hover:bg-accent-strong"
            >
              {t.clearAllFilters}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
