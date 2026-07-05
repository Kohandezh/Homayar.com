"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useCart } from "@/lib/cart";
import { useLocale } from "@/lib/locale";
import type { Product } from "@/lib/types";
import { CheckIcon } from "@/components/icons";

const LABELS = {
  fa: {
    talkToSales: "گفت‌وگو با فروش",
    added: "به سبد افزوده شد",
    addToCart: "افزودن به سبد",
  },
  en: {
    talkToSales: "Talk to sales",
    added: "Added to cart",
    addToCart: "Add to cart",
  },
} as const;

/**
 * Add-to-cart control. Custom-priced products route to sales instead.
 * size "sm" for cards, "lg" for the product detail page.
 */
export function AddToCartButton({
  product,
  size = "sm",
}: {
  product: Product;
  size?: "sm" | "lg";
}) {
  const { add } = useCart();
  const { locale } = useLocale();
  const t = locale === "fa" ? LABELS.fa : LABELS.en;
  const [added, setAdded] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  const sizing =
    size === "lg"
      ? "h-12 px-6 text-base rounded-xl"
      : "h-9 px-3.5 text-sm rounded-lg";

  if (product.billingType === "custom" || product.priceUsd === 0) {
    return (
      <Link
        href="/contact"
        className={`inline-flex items-center justify-center gap-2 border border-line-strong font-semibold text-ink transition-colors hover:border-accent hover:text-accent ${sizing}`}
      >
        {t.talkToSales}
      </Link>
    );
  }

  return (
    <button
      type="button"
      onClick={() => {
        add(product);
        setAdded(true);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => setAdded(false), 2000);
      }}
      aria-live="polite"
      className={`inline-flex items-center justify-center gap-2 font-semibold transition-colors ${sizing} ${
        added
          ? "bg-success/15 text-success border border-success/40"
          : "bg-accent text-bg hover:bg-accent-strong"
      }`}
    >
      {added ? (
        <>
          <CheckIcon width={size === "lg" ? 18 : 15} height={size === "lg" ? 18 : 15} />
          {t.added}
        </>
      ) : (
        t.addToCart
      )}
    </button>
  );
}
