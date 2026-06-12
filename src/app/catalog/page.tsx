import type { Metadata } from "next";
import { Suspense } from "react";
import { PageHero } from "@/components/ui";
import { getCategories, getProducts } from "@/lib/catalog";
import { CatalogExplorer } from "@/components/catalog/explorer";

export const metadata: Metadata = {
  title: "Product Catalog",
  description:
    "Browse 30 Homayar modules, platform plans, and services — AI voice analysis, automated quality scoring, compliance monitoring, coaching programs, and enterprise deployment. Filter by category, price, billing type, team size, and industry.",
};

function ExplorerFallback() {
  return (
    <div
      aria-hidden
      className="lg:grid lg:grid-cols-[280px_1fr] lg:gap-10 xl:grid-cols-[300px_1fr]"
    >
      <div className="hidden space-y-4 lg:block">
        {Array.from({ length: 5 }).map((_, i) => (
          <div
            key={i}
            className="h-24 animate-pulse rounded-xl border border-line bg-surface"
          />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="h-80 animate-pulse rounded-xl border border-line bg-surface"
          />
        ))}
      </div>
    </div>
  );
}

export default async function CatalogPage() {
  const [products, categories] = await Promise.all([
    getProducts(),
    getCategories(),
  ]);

  return (
    <>
      <PageHero
        eyebrow="Catalog"
        title="Modules, plans, and services"
        description="Every Homayar capability in one place — platform subscriptions, AI voice analysis modules, compliance and coaching services, and enterprise deployment options. Filter by what your team needs; every result links to full specs and pricing."
      />
      <section aria-label="Product catalog">
        <div className="container-site py-12 sm:py-16">
          <Suspense fallback={<ExplorerFallback />}>
            <CatalogExplorer products={products} categories={categories} />
          </Suspense>
        </div>
      </section>
    </>
  );
}
