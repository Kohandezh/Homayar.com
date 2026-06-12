import type { MetadataRoute } from "next";
import { getBlogPosts, getCaseStudies, getProducts } from "@/lib/catalog";
import { absoluteUrl } from "@/lib/site";

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const [products, caseStudies, posts] = await Promise.all([
    getProducts(),
    getCaseStudies(),
    getBlogPosts(),
  ]);

  const staticPages = [
    "",
    "/platform",
    "/ai-agents",
    "/solutions",
    "/pricing",
    "/catalog",
    "/case-studies",
    "/about",
    "/contact",
    "/faq",
    "/blog",
    "/privacy",
    "/terms",
  ].map((path) => ({
    url: absoluteUrl(path || "/"),
    changeFrequency: "weekly" as const,
    priority: path === "" ? 1 : 0.7,
  }));

  return [
    ...staticPages,
    ...products.map((p) => ({
      url: absoluteUrl(`/catalog/${p.slug}`),
      changeFrequency: "weekly" as const,
      priority: 0.8,
    })),
    ...caseStudies.map((c) => ({
      url: absoluteUrl(`/case-studies/${c.slug}`),
      changeFrequency: "monthly" as const,
      priority: 0.6,
    })),
    ...posts.map((b) => ({
      url: absoluteUrl(`/blog/${b.slug}`),
      changeFrequency: "monthly" as const,
      priority: 0.6,
    })),
  ];
}
