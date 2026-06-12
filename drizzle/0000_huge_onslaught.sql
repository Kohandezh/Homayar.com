CREATE TABLE "blog_posts" (
	"id" serial PRIMARY KEY NOT NULL,
	"slug" text NOT NULL,
	"title" text NOT NULL,
	"excerpt" text NOT NULL,
	"content_markdown" text NOT NULL,
	"author" text NOT NULL,
	"date_iso" text NOT NULL,
	"tags" jsonb NOT NULL,
	"reading_minutes" integer NOT NULL,
	"seo_title" text NOT NULL,
	"seo_description" text NOT NULL,
	CONSTRAINT "blog_posts_slug_unique" UNIQUE("slug")
);
--> statement-breakpoint
CREATE TABLE "case_studies" (
	"id" serial PRIMARY KEY NOT NULL,
	"slug" text NOT NULL,
	"company" text NOT NULL,
	"industry" text NOT NULL,
	"logo_initials" text NOT NULL,
	"headline" text NOT NULL,
	"summary" text NOT NULL,
	"challenge" text NOT NULL,
	"solution" text NOT NULL,
	"results" jsonb NOT NULL,
	"quote" text NOT NULL,
	"quote_author" text NOT NULL,
	"team_size" text NOT NULL,
	"seo_title" text NOT NULL,
	"seo_description" text NOT NULL,
	CONSTRAINT "case_studies_slug_unique" UNIQUE("slug")
);
--> statement-breakpoint
CREATE TABLE "categories" (
	"id" serial PRIMARY KEY NOT NULL,
	"slug" text NOT NULL,
	"name" text NOT NULL,
	"description" text NOT NULL,
	"icon" text NOT NULL,
	CONSTRAINT "categories_slug_unique" UNIQUE("slug")
);
--> statement-breakpoint
CREATE TABLE "checkout_sessions" (
	"id" serial PRIMARY KEY NOT NULL,
	"stripe_session_id" text NOT NULL,
	"status" text DEFAULT 'created' NOT NULL,
	"cart" jsonb NOT NULL,
	"created_at" timestamp DEFAULT now() NOT NULL,
	CONSTRAINT "checkout_sessions_stripe_session_id_unique" UNIQUE("stripe_session_id")
);
--> statement-breakpoint
CREATE TABLE "demo_requests" (
	"id" serial PRIMARY KEY NOT NULL,
	"name" text NOT NULL,
	"email" text NOT NULL,
	"company" text,
	"team_size" text,
	"message" text,
	"created_at" timestamp DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "leads" (
	"id" serial PRIMARY KEY NOT NULL,
	"name" text NOT NULL,
	"email" text NOT NULL,
	"company" text,
	"team_size" text,
	"message" text,
	"source" text DEFAULT 'contact' NOT NULL,
	"created_at" timestamp DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "order_items" (
	"id" serial PRIMARY KEY NOT NULL,
	"order_id" integer NOT NULL,
	"product_slug" text NOT NULL,
	"name" text NOT NULL,
	"unit_amount" integer NOT NULL,
	"quantity" integer DEFAULT 1 NOT NULL
);
--> statement-breakpoint
CREATE TABLE "orders" (
	"id" serial PRIMARY KEY NOT NULL,
	"stripe_session_id" text NOT NULL,
	"email" text,
	"amount_total" integer NOT NULL,
	"currency" text DEFAULT 'usd' NOT NULL,
	"status" text DEFAULT 'paid' NOT NULL,
	"payment_intent_id" text,
	"metadata" jsonb,
	"created_at" timestamp DEFAULT now() NOT NULL,
	CONSTRAINT "orders_stripe_session_id_unique" UNIQUE("stripe_session_id")
);
--> statement-breakpoint
CREATE TABLE "product_images" (
	"id" serial PRIMARY KEY NOT NULL,
	"product_id" integer NOT NULL,
	"url" text NOT NULL,
	"alt" text NOT NULL,
	"sort_order" integer DEFAULT 0 NOT NULL
);
--> statement-breakpoint
CREATE TABLE "products" (
	"id" serial PRIMARY KEY NOT NULL,
	"slug" text NOT NULL,
	"name" text NOT NULL,
	"category_slug" text NOT NULL,
	"tagline" text NOT NULL,
	"short_description" text NOT NULL,
	"long_description" text NOT NULL,
	"who_its_for" text NOT NULL,
	"key_outcomes" jsonb NOT NULL,
	"price_usd" integer NOT NULL,
	"billing_type" text NOT NULL,
	"features" jsonb NOT NULL,
	"seo_title" text NOT NULL,
	"seo_description" text NOT NULL,
	"seo_keywords" jsonb NOT NULL,
	"team_size" jsonb NOT NULL,
	"industries" jsonb NOT NULL,
	"popular" boolean DEFAULT false NOT NULL,
	"recommended" boolean DEFAULT false NOT NULL,
	"image_concept" text NOT NULL,
	"image" text NOT NULL,
	"created_at" timestamp DEFAULT now() NOT NULL,
	CONSTRAINT "products_slug_unique" UNIQUE("slug")
);
--> statement-breakpoint
ALTER TABLE "order_items" ADD CONSTRAINT "order_items_order_id_orders_id_fk" FOREIGN KEY ("order_id") REFERENCES "public"."orders"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "product_images" ADD CONSTRAINT "product_images_product_id_products_id_fk" FOREIGN KEY ("product_id") REFERENCES "public"."products"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "products" ADD CONSTRAINT "products_category_slug_categories_slug_fk" FOREIGN KEY ("category_slug") REFERENCES "public"."categories"("slug") ON DELETE no action ON UPDATE no action;