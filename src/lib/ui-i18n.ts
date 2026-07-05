import type { Locale } from "@/lib/content";
import type { BillingType } from "@/lib/types";

/**
 * Shared, locale-aware UI vocabulary for commerce chrome (badges, billing
 * labels, price formatting) used across product cards, the cart, and checkout.
 * Prices stay in USD with Western numerals — they are an international figure —
 * but the billing cadence and "Custom" wording localize. English is canonical;
 * fa overrides; every other locale falls back to English.
 */

const BILLING_LABELS_FA: Record<BillingType, string> = {
  monthly: "/ماه",
  yearly: "/سال",
  "one-time": "تک‌خرید",
  implementation: "پیاده‌سازی",
  custom: "قیمت‌گذاری سفارشی",
};

const BILLING_LABELS_EN: Record<BillingType, string> = {
  monthly: "/month",
  yearly: "/year",
  "one-time": "one-time",
  implementation: "implementation",
  custom: "custom pricing",
};

export function billingLabel(billingType: BillingType, locale: Locale): string {
  return locale === "fa"
    ? BILLING_LABELS_FA[billingType]
    : BILLING_LABELS_EN[billingType];
}

/** Locale-aware price string. USD figure stays Latin; only words localize. */
export function formatPriceL(
  priceUsd: number,
  billingType: BillingType,
  locale: Locale,
): string {
  if (billingType === "custom" || priceUsd === 0) {
    return locale === "fa" ? "سفارشی" : "Custom";
  }
  const amount = priceUsd.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
  if (billingType === "monthly") return locale === "fa" ? `${amount}/ماه` : `${amount}/mo`;
  if (billingType === "yearly") return locale === "fa" ? `${amount}/سال` : `${amount}/yr`;
  return amount;
}

const BADGES_FA = { popular: "پرطرفدار", recommended: "پیشنهادی" };
const BADGES_EN = { popular: "Popular", recommended: "Recommended" };

export function badgeLabels(locale: Locale) {
  return locale === "fa" ? BADGES_FA : BADGES_EN;
}
