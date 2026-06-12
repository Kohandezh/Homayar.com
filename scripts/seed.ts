/**
 * Seeds Neon with the full Homayar catalog and content.
 * Usage:  pnpm db:push && pnpm db:seed   (requires DATABASE_URL in .env.local)
 * Idempotent: clears and re-inserts content tables (never touches orders/leads).
 */
import { neon } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-http";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import * as schema from "../src/db/schema";

const root = join(import.meta.dirname, "..");
const readJson = <T>(rel: string): T =>
  JSON.parse(readFileSync(join(root, rel), "utf8")) as T;

async function main() {
  const url = process.env.DATABASE_URL;
  if (!url) {
    console.error("DATABASE_URL is not set. Add it to .env.local first.");
    process.exit(1);
  }
  const db = drizzle(neon(url), { schema });

  type RawProduct = {
    slug: string;
    name: string;
    category: string;
    tagline: string;
    shortDescription: string;
    longDescription: string;
    whoItsFor: string;
    keyOutcomes: string[];
    priceUsd: number;
    billingType: string;
    features: string[];
    seoTitle: string;
    seoDescription: string;
    seoKeywords: string[];
    teamSize: string[];
    industries: string[];
    popular: boolean;
    recommended: boolean;
    imageConcept: string;
  };

  const products = readJson<RawProduct[]>("src/data/products.json");
  const categories = readJson<
    { slug: string; name: string; description: string; icon: string }[]
  >("src/data/categories.json");
  const caseStudies = readJson<(typeof schema.caseStudies.$inferInsert)[]>(
    "content/case-studies.json",
  );
  const blogPosts = readJson<
    {
      slug: string;
      title: string;
      excerpt: string;
      contentMarkdown: string;
      author: string;
      dateISO: string;
      tags: string[];
      readingMinutes: number;
      seoTitle: string;
      seoDescription: string;
    }[]
  >("content/blog-posts.json");

  console.log("Clearing content tables…");
  await db.delete(schema.productImages);
  await db.delete(schema.products);
  await db.delete(schema.categories);
  await db.delete(schema.blogPosts);
  await db.delete(schema.caseStudies);

  console.log(`Inserting ${categories.length} categories…`);
  await db.insert(schema.categories).values(categories);

  console.log(`Inserting ${products.length} products…`);
  const inserted = await db
    .insert(schema.products)
    .values(
      products.map((p) => ({
        slug: p.slug,
        name: p.name,
        categorySlug: p.category,
        tagline: p.tagline,
        shortDescription: p.shortDescription,
        longDescription: p.longDescription,
        whoItsFor: p.whoItsFor,
        keyOutcomes: p.keyOutcomes,
        priceUsd: p.priceUsd,
        billingType: p.billingType,
        features: p.features,
        seoTitle: p.seoTitle,
        seoDescription: p.seoDescription,
        seoKeywords: p.seoKeywords,
        teamSize: p.teamSize,
        industries: p.industries,
        popular: p.popular,
        recommended: p.recommended,
        imageConcept: p.imageConcept,
        image: `/products/${p.slug}.png`,
      })),
    )
    .returning({ id: schema.products.id, slug: schema.products.slug, name: schema.products.name });

  console.log("Inserting product images…");
  await db.insert(schema.productImages).values(
    inserted.map((p) => ({
      productId: p.id,
      url: `/products/${p.slug}.png`,
      alt: `${p.name} — Homayar module artwork`,
      sortOrder: 0,
    })),
  );

  console.log(`Inserting ${blogPosts.length} blog posts…`);
  await db.insert(schema.blogPosts).values(blogPosts);

  console.log(`Inserting ${caseStudies.length} case studies…`);
  await db.insert(schema.caseStudies).values(caseStudies);

  console.log("✓ Seed complete.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
