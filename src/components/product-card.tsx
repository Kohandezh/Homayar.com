import Image from "next/image";
import Link from "next/link";
import type { Product } from "@/lib/types";
import { formatPrice, BILLING_LABELS } from "@/lib/types";
import { Badge } from "@/components/ui";
import { AddToCartButton } from "@/components/add-to-cart";

export function ProductCard({
  product,
  categoryName,
}: {
  product: Product;
  categoryName?: string;
}) {
  return (
    <article className="card-surface group flex flex-col overflow-hidden transition-colors hover:border-line-strong">
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
            {product.popular && <Badge tone="amber">Popular</Badge>}
            {product.recommended && <Badge tone="accent">Recommended</Badge>}
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
              {formatPrice(product.priceUsd, product.billingType)}
            </p>
            {(product.billingType === "one-time" ||
              product.billingType === "implementation") && (
              <p className="text-xs text-ink-faint">
                {BILLING_LABELS[product.billingType]}
              </p>
            )}
          </div>
          <AddToCartButton product={product} />
        </div>
      </div>
    </article>
  );
}
