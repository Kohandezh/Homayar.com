import type { Metadata } from "next";
import { getCategories, getProducts } from "@/lib/catalog";
import { PricingContent } from "./content";

export const metadata: Metadata = {
  title: "تعرفه‌ها — همایار",
  description:
    "Transparent plans for agentic AI voice analysis. Starter from $499/mo, Professional at $1,499/mo, and Enterprise with custom volume pricing. Extend any plan with specialized modules.",
};

export default async function PricingPage() {
  const [products, categories] = await Promise.all([
    getProducts(),
    getCategories(),
  ]);

  return <PricingContent products={products} categories={categories} />;
}
