"use client";

import Image from "next/image";
import Link from "next/link";
import type { Product } from "@/lib/types";
import { useLocale } from "@/lib/locale";
import { badgeLabels, billingLabel, formatPriceL } from "@/lib/ui-i18n";
import { Badge } from "@/components/ui";
import { AddToCartButton } from "@/components/add-to-cart";

export function ProductCard({
  product,
  categoryName,
}: {
  product: Product;
  categoryName?: string;
}) {
  const { locale } = useLocale();
  const badges = badgeLabels(locale);
  return (
    <article className="card-surface lift liquid-edge group flex flex-col overflow-hidden">
      <Link
        href={`/catalog/${product.slug}`}
        className="relative block aspect-[4/3] overflow-hidden border-b border-line"
        aria-label={`${product.name} details`}
      >
        <Image
          src={product.image}
          alt={`${product.name} artwork`}
          fill
          sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 33vw"
          className="object-cover transition-transform duration-500 group-hover:scale-[1.03]"
        />
        {(product.popular || product.recommended) && (
          <div className="absolute left-3 top-3 flex gap-2">
            {product.popular && <Badge tone="amber">{badges.popular}</Badge>}
            {product.recommended && (
              <Badge tone="accent">{badges.recommended}</Badge>
            )}
          </div>
        )}
      </Link>

      <div className="flex flex-1 flex-col p-5">
        {categoryName && (
          <p className="text-xs font-medium uppercase tracking-wider text-ink-faint">
            {categoryName}
          </p>
        )}
        <h3 className="mt-1.5 font-display text-lg font-semibold leading-snug text-ink">
          <Link
            href={`/catalog/${product.slug}`}
            className="hover:text-accent transition-colors"
          >
            {product.name}
          </Link>
        </h3>
        <p className="mt-1.5 line-clamp-2 text-sm leading-relaxed text-ink-muted">
          {product.shortDescription}
        </p>

        <div className="mt-auto flex items-end justify-between gap-3 pt-5">
          <div>
            <p className="font-mono text-lg font-semibold text-ink">
              {formatPriceL(product.priceUsd, product.billingType, locale)}
            </p>
            {(product.billingType === "one-time" ||
              product.billingType === "implementation") && (
              <p className="text-xs text-ink-faint">
                {billingLabel(product.billingType, locale)}
              </p>
            )}
          </div>
          <AddToCartButton product={product} />
        </div>
      </div>
    </article>
  );
}
