import type { Locale } from "@/lib/content";
import type { BlogPost, CaseStudy, Category, Product } from "@/lib/types";

import productsFa from "@content/i18n/products.fa.json";
import categoriesFa from "@content/i18n/categories.fa.json";
import caseStudiesFa from "@content/i18n/case-studies.fa.json";
import blogPostsFa from "@content/i18n/blog-posts.fa.json";

/**
 * Data localization layer. English JSON (src/data, content/*.json) is the
 * canonical source; Persian overrides live in content/i18n/*.fa.json keyed by
 * slug. These helpers merge the fa override onto the canonical record field by
 * field, so a missing or empty fa value transparently falls back to English and
 * a half-translated file never blanks out a page. Any non-fa locale returns the
 * canonical record unchanged (the site is fa-default + en, others fall back to
 * en — matching the page-content dictionaries).
 *
 * Locale is client-only state (see @/lib/locale), so call these inside client
 * components that read useLocale(). Server components pass the canonical record
 * straight through.
 */

// Per-entity fa override shapes: only the human-readable fields are translated.
// Identifiers (slug, category), numbers, dates, image paths and proper nouns
// stay canonical.
type ProductFa = Partial<
  Pick<
    Product,
    | "name"
    | "tagline"
    | "shortDescription"
    | "longDescription"
    | "whoItsFor"
    | "keyOutcomes"
    | "features"
    | "seoTitle"
    | "seoDescription"
  >
>;
type CategoryFa = Partial<Pick<Category, "name" | "description">>;
type CaseStudyFa = Partial<
  Pick<
    CaseStudy,
    | "company"
    | "headline"
    | "summary"
    | "challenge"
    | "solution"
    | "results"
    | "quote"
    | "quoteAuthor"
    | "teamSize"
    | "seoTitle"
    | "seoDescription"
  >
>;
type BlogPostFa = Partial<
  Pick<
    BlogPost,
    | "title"
    | "excerpt"
    | "contentMarkdown"
    | "author"
    | "tags"
    | "seoTitle"
    | "seoDescription"
  >
>;

const PRODUCTS_FA = productsFa as Record<string, ProductFa>;
const CATEGORIES_FA = categoriesFa as Record<string, CategoryFa>;
const CASE_STUDIES_FA = caseStudiesFa as Record<string, CaseStudyFa>;
const BLOG_POSTS_FA = blogPostsFa as Record<string, BlogPostFa>;

/** True when a translated value is present and worth using over the canonical. */
function present(value: unknown): boolean {
  if (value == null) return false;
  if (typeof value === "string") return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  return true;
}

/** Shallow-merge only the present override fields onto the canonical record. */
function merge<T extends object>(canonical: T, override: Partial<T> | undefined): T {
  if (!override) return canonical;
  const out = { ...canonical };
  for (const key of Object.keys(override) as (keyof T)[]) {
    const value = override[key];
    if (present(value)) out[key] = value as T[keyof T];
  }
  return out;
}

export function localizeProduct(product: Product, locale: Locale): Product {
  if (locale !== "fa") return product;
  return merge<Product>(product, PRODUCTS_FA[product.slug]);
}

export function localizeProducts(products: Product[], locale: Locale): Product[] {
  if (locale !== "fa") return products;
  return products.map((p) => localizeProduct(p, locale));
}

export function localizeCategory(category: Category, locale: Locale): Category {
  if (locale !== "fa") return category;
  return merge<Category>(category, CATEGORIES_FA[category.slug]);
}

export function localizeCategories(
  categories: Category[],
  locale: Locale,
): Category[] {
  if (locale !== "fa") return categories;
  return categories.map((c) => localizeCategory(c, locale));
}

export function localizeCaseStudy(study: CaseStudy, locale: Locale): CaseStudy {
  if (locale !== "fa") return study;
  return merge<CaseStudy>(study, CASE_STUDIES_FA[study.slug]);
}

export function localizeCaseStudies(
  studies: CaseStudy[],
  locale: Locale,
): CaseStudy[] {
  if (locale !== "fa") return studies;
  return studies.map((s) => localizeCaseStudy(s, locale));
}

export function localizeBlogPost(post: BlogPost, locale: Locale): BlogPost {
  if (locale !== "fa") return post;
  return merge<BlogPost>(post, BLOG_POSTS_FA[post.slug]);
}

export function localizeBlogPosts(posts: BlogPost[], locale: Locale): BlogPost[] {
  if (locale !== "fa") return posts;
  return posts.map((p) => localizeBlogPost(p, locale));
}
