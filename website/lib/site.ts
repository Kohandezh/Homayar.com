import type { Locale } from "./i18n";

export const siteUrl = (process.env.NEXT_PUBLIC_SITE_URL ?? "https://homayar.com").replace(/\/$/, "");

export const appUrl = process.env.NEXT_PUBLIC_APP_URL?.trim() || "/app";

export const solutionSlugs = ["qa-managers", "supervisors", "contact-center-operations", "it-security"] as const;
export type SolutionSlug = (typeof solutionSlugs)[number];

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
