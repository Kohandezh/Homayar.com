import type { Locale } from "./i18n";

/** Public origin used for canonical URLs, hreflang links and the sitemap. */
export const siteUrl = (process.env.NEXT_PUBLIC_SITE_URL ?? "https://homayar.com").replace(/\/$/, "");

/** Where "Log in" and "Start" send people. Defaults to the console path used by the deployment runbook. */
export const appUrl = process.env.NEXT_PUBLIC_APP_URL?.trim() || "/app";

export const solutionSlugs = ["qa-managers", "supervisors", "contact-center-operations", "it-security"] as const;
export type SolutionSlug = (typeof solutionSlugs)[number];

/** Every public page path without a locale prefix. The sitemap and the hreflang links are built from this list. */
export const sitePaths: string[] = [
  "",
  "/product",
  "/how-it-works",
  "/solutions",
  ...solutionSlugs.map((slug) => `/solutions/${slug}`),
  "/security",
  "/deployment",
  "/about",
  "/contact",
  "/pricing",
  "/legal/privacy",
  "/legal/terms",
  "/legal/gdpr",
  "/transparency",
];

export function localePath(locale: Locale, path: string): string {
  return `/${locale}${path}`;
}

export function absoluteUrl(locale: Locale, path: string): string {
  return `${siteUrl}${localePath(locale, path)}`;
}
