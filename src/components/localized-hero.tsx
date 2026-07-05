"use client";

import type { ReactNode } from "react";
import { PageHero } from "@/components/ui";
import { useLocale } from "@/lib/locale";

/**
 * Client wrapper around <PageHero> so data-driven server pages (catalog,
 * case-studies, blog) can localize their hero copy without becoming client
 * components wholesale. fa is the default locale; every non-fa locale falls
 * back to the English copy — same convention as the page content dictionaries.
 */
export type HeroCopy = { eyebrow?: string; title: string; description?: string };

export function LocalizedHero({
  copy,
  children,
}: {
  copy: { fa: HeroCopy; en: HeroCopy };
  children?: ReactNode;
}) {
  const { locale } = useLocale();
  const c = locale === "fa" ? copy.fa : copy.en;
  return (
    <PageHero eyebrow={c.eyebrow} title={c.title} description={c.description}>
      {children}
    </PageHero>
  );
}
