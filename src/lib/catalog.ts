import { asc } from "drizzle-orm";
import { getDb, hasDb } from "@/lib/db";
import * as schema from "@/db/schema";
import type {
  BlogPost,
  CaseStudy,
  Category,
  FaqItem,
  Product,
} from "@/lib/types";

import productsJson from "@/data/products.json";
import categoriesJson from "@/data/categories.json";
import caseStudiesJson from "@content/case-studies.json";
import blogPostsJson from "@content/blog-posts.json";
import faqAboutJson from "@content/faq-about.json";

/**
 * Data access layer. Reads from Neon (Drizzle) when DATABASE_URL is set,
 * otherwise serves the static seed data bundled with the app — the same data
 * the seed script loads into Neon, so both paths render identically.
 */

type RawProduct = Omit<Product, "image"> & { image?: string };

function withImage(p: RawProduct): Product {
  return { ...p, image: p.image ?? `/products/${p.slug}.png` };
}

const staticProducts: Product[] = (productsJson as RawProduct[]).map(withImage);
const staticCategories: Category[] = categoriesJson as Category[];
const staticCaseStudies: CaseStudy[] = caseStudiesJson as CaseStudy[];
const staticBlogPosts: BlogPost[] = blogPostsJson as BlogPost[];
const staticFaq: FaqItem[] = (faqAboutJson as { faq: FaqItem[] }).faq;

export const aboutContent = (
  faqAboutJson as {
    about: {
      missionHeadline: string;
      missionBody: string;
      nameStory: string;
      values: { name: string; description: string }[];
      companyNote: string;
    };
  }
).about;

export async function getProducts(): Promise<Product[]> {
  if (!hasDb()) return staticProducts;
  try {
    const rows = await getDb()
      .select()
      .from(schema.products)
      .orderBy(asc(schema.products.id));
    if (rows.length === 0) return staticProducts;
    return rows.map((r) =>
      withImage({
        slug: r.slug,
        name: r.name,
        category: r.categorySlug,
        tagline: r.tagline,
        shortDescription: r.shortDescription,
        longDescription: r.longDescription,
        whoItsFor: r.whoItsFor,
        keyOutcomes: r.keyOutcomes,
        priceUsd: r.priceUsd,
        billingType: r.billingType as Product["billingType"],
        features: r.features,
        seoTitle: r.seoTitle,
        seoDescription: r.seoDescription,
        seoKeywords: r.seoKeywords,
        teamSize: r.teamSize as Product["teamSize"],
        industries: r.industries as Product["industries"],
        popular: r.popular,
        recommended: r.recommended,
        imageConcept: r.imageConcept,
        image: r.image,
      }),
    );
  } catch (err) {
    console.error("[catalog] DB read failed, serving static data:", err);
    return staticProducts;
  }
}

export async function getProductBySlug(slug: string): Promise<Product | null> {
  const all = await getProducts();
  return all.find((p) => p.slug === slug) ?? null;
}

export async function getCategories(): Promise<Category[]> {
  if (!hasDb()) return staticCategories;
  try {
    const rows = await getDb()
      .select()
      .from(schema.categories)
      .orderBy(asc(schema.categories.id));
    if (rows.length === 0) return staticCategories;
    return rows.map(({ slug, name, description, icon }) => ({
      slug,
      name,
      description,
      icon,
    }));
  } catch (err) {
    console.error("[catalog] DB read failed, serving static data:", err);
    return staticCategories;
  }
}

export async function getCaseStudies(): Promise<CaseStudy[]> {
  if (!hasDb()) return staticCaseStudies;
  try {
    const rows = await getDb()
      .select()
      .from(schema.caseStudies)
      .orderBy(asc(schema.caseStudies.id));
    if (rows.length === 0) return staticCaseStudies;
    return rows.map((r) => ({
      slug: r.slug,
      company: r.company,
      industry: r.industry,
      logoInitials: r.logoInitials,
      headline: r.headline,
      summary: r.summary,
      challenge: r.challenge,
      solution: r.solution,
      results: r.results,
      quote: r.quote,
      quoteAuthor: r.quoteAuthor,
      teamSize: r.teamSize,
      seoTitle: r.seoTitle,
      seoDescription: r.seoDescription,
    }));
  } catch (err) {
    console.error("[catalog] DB read failed, serving static data:", err);
    return staticCaseStudies;
  }
}

export async function getCaseStudyBySlug(
  slug: string,
): Promise<CaseStudy | null> {
  const all = await getCaseStudies();
  return all.find((c) => c.slug === slug) ?? null;
}

export async function getBlogPosts(): Promise<BlogPost[]> {
  if (!hasDb()) return sortPosts(staticBlogPosts);
  try {
    const rows = await getDb().select().from(schema.blogPosts);
    if (rows.length === 0) return sortPosts(staticBlogPosts);
    return sortPosts(
      rows.map((r) => ({
        slug: r.slug,
        title: r.title,
        excerpt: r.excerpt,
        contentMarkdown: r.contentMarkdown,
        author: r.author,
        dateISO: r.dateISO,
        tags: r.tags,
        readingMinutes: r.readingMinutes,
        seoTitle: r.seoTitle,
        seoDescription: r.seoDescription,
      })),
    );
  } catch (err) {
    console.error("[catalog] DB read failed, serving static data:", err);
    return sortPosts(staticBlogPosts);
  }
}

function sortPosts(posts: BlogPost[]): BlogPost[] {
  return [...posts].sort((a, b) => b.dateISO.localeCompare(a.dateISO));
}

export async function getBlogPostBySlug(
  slug: string,
): Promise<BlogPost | null> {
  const all = await getBlogPosts();
  return all.find((p) => p.slug === slug) ?? null;
}

export function getFaq(): FaqItem[] {
  return staticFaq;
}
