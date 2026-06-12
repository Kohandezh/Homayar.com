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

/* ── Filter vocab ───────────────────────────────────────────────── */

const PRICE_RANGES = [
  { value: "under-300", label: "Under $300" },
  { value: "300-800", label: "$300 – $800" },
  { value: "over-800", label: "Over $800" },
  { value: "custom", label: "Custom pricing" },
] as const;

type PriceRange = (typeof PRICE_RANGES)[number]["value"];

const BILLING_OPTIONS: { value: BillingType; label: string }[] = [
  { value: "monthly", label: "Monthly" },
  { value: "yearly", label: "Yearly" },
  { value: "one-time", label: "One-time" },
  { value: "implementation", label: "Implementation" },
  { value: "custom", label: "Custom" },
];

const TEAM_OPTIONS: { value: TeamSize; label: string }[] = [
  { value: "startup", label: "Startup" },
  { value: "smb", label: "SMB" },
  { value: "mid-market", label: "Mid-market" },
  { value: "enterprise", label: "Enterprise" },
];

const INDUSTRY_OPTIONS: { value: Industry; label: string }[] = [
  { value: "banking", label: "Banking" },
  { value: "healthcare", label: "Healthcare" },
  { value: "insurance", label: "Insurance" },
  { value: "telecom", label: "Telecom" },
  { value: "retail", label: "Retail" },
  { value: "government", label: "Government" },
  { value: "technology", label: "Technology" },
];

const SORT_OPTIONS = [
  { value: "featured", label: "Featured" },
  { value: "price-asc", label: "Price: low to high" },
  { value: "price-desc", label: "Price: high to low" },
  { value: "name", label: "Name A–Z" },
] as const;

type SortKey = (typeof SORT_OPTIONS)[number]["value"];

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
  products,
  categories,
}: {
  products: Product[];
  categories: Category[];
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [mobileOpen, setMobileOpen] = useState(false);

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
  const price = parseOne(
    searchParams.get("price"),
    PRICE_RANGES.map((r) => r.value),
  );
  const billing = parseList(
    searchParams.get("billing"),
    BILLING_OPTIONS.map((o) => o.value),
  );
  const team = parseList(
    searchParams.get("team"),
    TEAM_OPTIONS.map((o) => o.value),
  );
  const industry = parseOne(
    searchParams.get("industry"),
    INDUSTRY_OPTIONS.map((o) => o.value),
  );
  const popular = searchParams.get("popular") === "1";
  const recommended = searchParams.get("recommended") === "1";
  const sort: SortKey =
    parseOne(
      searchParams.get("sort"),
      SORT_OPTIONS.map((o) => o.value),
    ) || "featured";

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

  const filtered = useMemo(() => {
    return products.filter((p) => {
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
  }, [products, category, price, billing, team, industry, popular, recommended]);

  const sorted = useMemo(() => {
    const arr = [...filtered];
    if (sort === "price-asc" || sort === "price-desc") {
      const eff = (p: Product) =>
        isCustomPriced(p) ? Number.POSITIVE_INFINITY : p.priceUsd;
      arr.sort((a, b) => {
        const ea = eff(a);
        const eb = eff(b);
        if (ea === eb) return 0;
        /* Custom-priced items always sink to the end of price sorts. */
        if (ea === Number.POSITIVE_INFINITY) return 1;
        if (eb === Number.POSITIVE_INFINITY) return -1;
        return sort === "price-asc" ? ea - eb : eb - ea;
      });
    } else if (sort === "name") {
      arr.sort((a, b) => a.name.localeCompare(b.name));
    } else {
      arr.sort((a, b) => featuredScore(b) - featuredScore(a));
    }
    return arr;
  }, [filtered, sort]);

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
      label: PRICE_RANGES.find((r) => r.value === price)?.label ?? price,
      onRemove: () => setParams({ price: null }),
    });
  }
  for (const b of billing) {
    pills.push({
      key: `billing-${b}`,
      label: BILLING_OPTIONS.find((o) => o.value === b)?.label ?? b,
      onRemove: () => toggleListValue("billing", b),
    });
  }
  for (const t of team) {
    pills.push({
      key: `team-${t}`,
      label: TEAM_OPTIONS.find((o) => o.value === t)?.label ?? t,
      onRemove: () => toggleListValue("team", t),
    });
  }
  if (industry) {
    pills.push({
      key: `industry-${industry}`,
      label:
        INDUSTRY_OPTIONS.find((o) => o.value === industry)?.label ?? industry,
      onRemove: () => setParams({ industry: null }),
    });
  }
  if (popular) {
    pills.push({
      key: "popular",
      label: "Popular",
      onRemove: () => setParams({ popular: null }),
    });
  }
  if (recommended) {
    pills.push({
      key: "recommended",
      label: "Recommended",
      onRemove: () => setParams({ recommended: null }),
    });
  }

  const activeCount = pills.length;

  /* ── Filter panel (rendered twice: mobile collapsible + lg sidebar) ── */

  const renderFilters = (idPrefix: string) => (
    <div className="space-y-7">
      <FilterGroup label="Category">
        <div className="flex flex-wrap gap-2">
          <Chip active={!category} onClick={() => setParams({ category: null })}>
            All
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
          Price range
        </label>
        <SelectWrap>
          <select
            id={`${idPrefix}-price`}
            value={price}
            onChange={(e) => setParams({ price: e.target.value || null })}
            className={SELECT_CLASSES}
          >
            <option value="">Any price</option>
            {PRICE_RANGES.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </SelectWrap>
      </div>

      <FilterGroup label="Billing type">
        <div className="flex flex-wrap gap-2">
          {BILLING_OPTIONS.map((o) => (
            <Chip
              key={o.value}
              active={billing.includes(o.value)}
              onClick={() => toggleListValue("billing", o.value)}
            >
              {o.label}
            </Chip>
          ))}
        </div>
      </FilterGroup>

      <FilterGroup label="Team size">
        <div className="flex flex-wrap gap-2">
          {TEAM_OPTIONS.map((o) => (
            <Chip
              key={o.value}
              active={team.includes(o.value)}
              onClick={() => toggleListValue("team", o.value)}
            >
              {o.label}
            </Chip>
          ))}
        </div>
      </FilterGroup>

      <div>
        <label
          htmlFor={`${idPrefix}-industry`}
          className="mb-2.5 block text-xs font-semibold uppercase tracking-wider text-ink-muted"
        >
          Industry
        </label>
        <SelectWrap>
          <select
            id={`${idPrefix}-industry`}
            value={industry}
            onChange={(e) => setParams({ industry: e.target.value || null })}
            className={SELECT_CLASSES}
          >
            <option value="">All industries</option>
            {INDUSTRY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </SelectWrap>
      </div>

      <FilterGroup label="Highlights">
        <div className="flex flex-wrap gap-2">
          <Chip
            active={popular}
            onClick={() => setParams({ popular: popular ? null : "1" })}
          >
            Popular
          </Chip>
          <Chip
            active={recommended}
            onClick={() =>
              setParams({ recommended: recommended ? null : "1" })
            }
          >
            Recommended
          </Chip>
        </div>
      </FilterGroup>

      {activeCount > 0 && (
        <button
          type="button"
          onClick={clearAll}
          className="text-sm font-medium text-ink-muted underline underline-offset-4 transition-colors hover:text-accent"
        >
          Clear all filters
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
            Filters
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
            Showing{" "}
            <span className="font-mono font-semibold text-ink">
              {sorted.length}
            </span>{" "}
            of{" "}
            <span className="font-mono font-semibold text-ink">
              {products.length}
            </span>{" "}
            products
          </p>
          <div className="flex items-center gap-2.5">
            <label
              htmlFor="catalog-sort"
              className="shrink-0 text-sm text-ink-muted"
            >
              Sort by
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
                {SORT_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
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
              Clear all
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
              No products match these filters
            </p>
            <p className="max-w-md text-sm leading-relaxed text-ink-muted">
              Try removing a filter or two — every Homayar module, plan, and
              service is one click away.
            </p>
            <button
              type="button"
              onClick={clearAll}
              className="inline-flex h-10 items-center justify-center rounded-lg bg-accent px-4 text-sm font-semibold text-bg transition-colors hover:bg-accent-strong"
            >
              Clear all filters
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
