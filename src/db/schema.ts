import {
  boolean,
  integer,
  jsonb,
  pgTable,
  serial,
  text,
  timestamp,
} from "drizzle-orm/pg-core";

export const categories = pgTable("categories", {
  id: serial("id").primaryKey(),
  slug: text("slug").notNull().unique(),
  name: text("name").notNull(),
  description: text("description").notNull(),
  icon: text("icon").notNull(),
});

export const products = pgTable("products", {
  id: serial("id").primaryKey(),
  slug: text("slug").notNull().unique(),
  name: text("name").notNull(),
  categorySlug: text("category_slug")
    .notNull()
    .references(() => categories.slug),
  tagline: text("tagline").notNull(),
  shortDescription: text("short_description").notNull(),
  longDescription: text("long_description").notNull(),
  whoItsFor: text("who_its_for").notNull(),
  keyOutcomes: jsonb("key_outcomes").$type<string[]>().notNull(),
  priceUsd: integer("price_usd").notNull(),
  billingType: text("billing_type").notNull(),
  features: jsonb("features").$type<string[]>().notNull(),
  seoTitle: text("seo_title").notNull(),
  seoDescription: text("seo_description").notNull(),
  seoKeywords: jsonb("seo_keywords").$type<string[]>().notNull(),
  teamSize: jsonb("team_size").$type<string[]>().notNull(),
  industries: jsonb("industries").$type<string[]>().notNull(),
  popular: boolean("popular").notNull().default(false),
  recommended: boolean("recommended").notNull().default(false),
  imageConcept: text("image_concept").notNull(),
  image: text("image").notNull(),
  createdAt: timestamp("created_at").notNull().defaultNow(),
});

export const productImages = pgTable("product_images", {
  id: serial("id").primaryKey(),
  productId: integer("product_id")
    .notNull()
    .references(() => products.id, { onDelete: "cascade" }),
  url: text("url").notNull(),
  alt: text("alt").notNull(),
  sortOrder: integer("sort_order").notNull().default(0),
});

export const leads = pgTable("leads", {
  id: serial("id").primaryKey(),
  name: text("name").notNull(),
  // Nullable: many inbound leads (esp. fa locale) leave a phone only. The phone
  // is preserved in `message`, so a missing email must never drop the lead.
  email: text("email"),
  company: text("company"),
  teamSize: text("team_size"),
  message: text("message"),
  source: text("source").notNull().default("contact"),
  createdAt: timestamp("created_at").notNull().defaultNow(),
});

export const demoRequests = pgTable("demo_requests", {
  id: serial("id").primaryKey(),
  name: text("name").notNull(),
  email: text("email").notNull(),
  company: text("company"),
  teamSize: text("team_size"),
  message: text("message"),
  createdAt: timestamp("created_at").notNull().defaultNow(),
});

export const bookings = pgTable("bookings", {
  id: serial("id").primaryKey(),
  name: text("name").notNull(),
  email: text("email").notNull(),
  company: text("company"),
  message: text("message"),
  // The booked slot as an absolute instant. Unique so the DB itself rejects a
  // double-booking even under a race between two concurrent POSTs.
  startsAt: timestamp("starts_at", { withTimezone: true }).notNull().unique(),
  endsAt: timestamp("ends_at", { withTimezone: true }).notNull(),
  // The visitor's IANA timezone, kept for reference / reminders.
  timezone: text("timezone"),
  status: text("status").notNull().default("confirmed"),
  createdAt: timestamp("created_at").notNull().defaultNow(),
});

export const checkoutSessions = pgTable("checkout_sessions", {
  id: serial("id").primaryKey(),
  stripeSessionId: text("stripe_session_id").notNull().unique(),
  status: text("status").notNull().default("created"),
  cart: jsonb("cart")
    .$type<{ slug: string; quantity: number; unitAmount: number }[]>()
    .notNull(),
  createdAt: timestamp("created_at").notNull().defaultNow(),
});

export const orders = pgTable("orders", {
  id: serial("id").primaryKey(),
  stripeSessionId: text("stripe_session_id").notNull().unique(),
  email: text("email"),
  amountTotal: integer("amount_total").notNull(),
  currency: text("currency").notNull().default("usd"),
  status: text("status").notNull().default("paid"),
  paymentIntentId: text("payment_intent_id"),
  metadata: jsonb("metadata").$type<Record<string, string>>(),
  createdAt: timestamp("created_at").notNull().defaultNow(),
});

export const orderItems = pgTable("order_items", {
  id: serial("id").primaryKey(),
  orderId: integer("order_id")
    .notNull()
    .references(() => orders.id, { onDelete: "cascade" }),
  productSlug: text("product_slug").notNull(),
  name: text("name").notNull(),
  unitAmount: integer("unit_amount").notNull(),
  quantity: integer("quantity").notNull().default(1),
});

export const blogPosts = pgTable("blog_posts", {
  id: serial("id").primaryKey(),
  slug: text("slug").notNull().unique(),
  title: text("title").notNull(),
  excerpt: text("excerpt").notNull(),
  contentMarkdown: text("content_markdown").notNull(),
  author: text("author").notNull(),
  dateISO: text("date_iso").notNull(),
  tags: jsonb("tags").$type<string[]>().notNull(),
  readingMinutes: integer("reading_minutes").notNull(),
  seoTitle: text("seo_title").notNull(),
  seoDescription: text("seo_description").notNull(),
});

export const caseStudies = pgTable("case_studies", {
  id: serial("id").primaryKey(),
  slug: text("slug").notNull().unique(),
  company: text("company").notNull(),
  industry: text("industry").notNull(),
  logoInitials: text("logo_initials").notNull(),
  headline: text("headline").notNull(),
  summary: text("summary").notNull(),
  challenge: text("challenge").notNull(),
  solution: text("solution").notNull(),
  results: jsonb("results")
    .$type<{ metric: string; value: string }[]>()
    .notNull(),
  quote: text("quote").notNull(),
  quoteAuthor: text("quote_author").notNull(),
  teamSize: text("team_size").notNull(),
  seoTitle: text("seo_title").notNull(),
  seoDescription: text("seo_description").notNull(),
});
