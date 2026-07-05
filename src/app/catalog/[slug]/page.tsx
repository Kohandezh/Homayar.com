import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getCategories, getProductBySlug, getProducts } from "@/lib/catalog";
import { localizeProduct } from "@/lib/data-i18n";
import { absoluteUrl } from "@/lib/site";
import type { Product } from "@/lib/types";
import { ProductDetail } from "./detail";

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
  // fa is the default locale, so default SEO metadata is the Persian copy.
  const fa = localizeProduct(product, "fa");
  return {
    title: fa.seoTitle,
    description: fa.seoDescription,
    keywords: product.seoKeywords,
    alternates: { canonical: absoluteUrl(`/catalog/${product.slug}`) },
    openGraph: {
      title: fa.seoTitle,
      description: fa.seoDescription,
      images: [{ url: product.image, alt: `${fa.name} artwork` }],
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

  const related = relatedProducts(allProducts, product);
  const isCustom = product.billingType === "custom" || product.priceUsd === 0;
  const fa = localizeProduct(product, "fa");

  const jsonLd: Record<string, unknown> = {
    "@context": "https://schema.org",
    "@type": "Product",
    name: fa.name,
    description: fa.seoDescription,
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
      <ProductDetail
        product={product}
        categories={categories}
        related={related}
      />
    </>
  );
}
