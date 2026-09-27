import type { Metadata } from "next";
import type { Locale } from "./i18n";
import { absoluteUrl, siteUrl } from "./site";

export function pageMetadata({
  locale,
  path,
  title,
  description,
  siteName,
}: {
  locale: Locale;
  path: string;
  title: string;
  description: string;
  siteName: string;
}): Metadata {
  return {
    title,
    description,
    metadataBase: new URL(siteUrl),
    alternates: {
      canonical: absoluteUrl(locale, path),
      languages: {
        fa: absoluteUrl("fa", path),
        en: absoluteUrl("en", path),
        "x-default": absoluteUrl("fa", path),
      },
    },
    openGraph: {
      title,
      description,
      siteName,
      type: "website",
      locale: locale === "fa" ? "fa_IR" : "en_US",
      alternateLocale: locale === "fa" ? "en_US" : "fa_IR",
      url: absoluteUrl(locale, path),
    },
    twitter: {
      card: "summary",
      title,
      description,
    },
  };
}
