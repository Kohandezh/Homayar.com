import type { MetadataRoute } from "next";
import { locales } from "@/lib/i18n";
import { absoluteUrl, sitePaths } from "@/lib/site";

export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date("2026-09-27");
  return sitePaths.flatMap((path) =>
    locales.map((locale) => ({
      url: absoluteUrl(locale, path),
      lastModified,
      changeFrequency: path === "" ? ("weekly" as const) : ("monthly" as const),
      priority: path === "" ? 1 : path.startsWith("/legal") ? 0.3 : 0.7,
      alternates: {
        languages: {
          fa: absoluteUrl("fa", path),
          en: absoluteUrl("en", path),
          "x-default": absoluteUrl("fa", path),
        },
      },
    })),
  );
}
