import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getBlogPostBySlug, getBlogPosts } from "@/lib/catalog";
import { renderMarkdown } from "@/lib/markdown";
import { Badge, ButtonLink } from "@/components/ui";
import { ArrowRightIcon } from "@/components/icons";
import { SITE, absoluteUrl } from "@/lib/site";

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
] as const;

function formatDate(dateISO: string): string {
  const [year, month, day] = dateISO.split("-").map(Number);
  return `${MONTHS[(month ?? 1) - 1]} ${day}, ${year}`;
}

export async function generateStaticParams() {
  const posts = await getBlogPosts();
  return posts.map((post) => ({ slug: post.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const post = await getBlogPostBySlug(slug);
  if (!post) return { title: "Post not found" };
  return {
    title: { absolute: post.seoTitle },
    description: post.seoDescription,
    alternates: { canonical: absoluteUrl(`/blog/${post.slug}`) },
    openGraph: {
      type: "article",
      title: post.seoTitle,
      description: post.seoDescription,
      url: absoluteUrl(`/blog/${post.slug}`),
      publishedTime: post.dateISO,
      authors: [post.author],
      tags: post.tags,
    },
  };
}

export default async function BlogPostPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const post = await getBlogPostBySlug(slug);
  if (!post) notFound();

  const posts = await getBlogPosts();
  const index = posts.findIndex((p) => p.slug === post.slug);
  const others = posts.filter((p) => p.slug !== post.slug);
  const moreInsights = [
    ...others.slice(index),
    ...others.slice(0, index),
  ].slice(0, 2);

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "Article",
    headline: post.title,
    description: post.seoDescription,
    datePublished: post.dateISO,
    author: {
      "@type": "Person",
      name: post.author.split(",")[0],
    },
    publisher: {
      "@type": "Organization",
      name: SITE.name,
      url: SITE.url,
    },
    mainEntityOfPage: absoluteUrl(`/blog/${post.slug}`),
  };

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <article className="py-12 sm:py-16">
        <div className="container-site">
          <header className="mx-auto max-w-3xl">
            <div className="flex flex-wrap gap-2">
              {post.tags.map((tag) => (
                <Badge key={tag} tone="accent">
                  {tag}
                </Badge>
              ))}
            </div>
            <h1 className="mt-6 font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl lg:text-5xl">
              {post.title}
            </h1>
            <p className="mt-6 flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line pb-8 text-sm text-ink-muted">
              <span className="text-ink-soft">{post.author}</span>
              <span aria-hidden>·</span>
              <time dateTime={post.dateISO} className="font-mono">
                {formatDate(post.dateISO)}
              </time>
              <span aria-hidden>·</span>
              <span className="font-mono">{post.readingMinutes} min read</span>
            </p>
          </header>

          <div
            className="prose-dark mx-auto mt-10 max-w-3xl"
            dangerouslySetInnerHTML={{
              __html: renderMarkdown(post.contentMarkdown),
            }}
          />
        </div>
      </article>

      <section
        aria-labelledby="more-insights-heading"
        className="border-t border-line py-12 sm:py-16"
      >
        <div className="container-site">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <h2
              id="more-insights-heading"
              className="font-display text-2xl font-semibold tracking-tight text-ink"
            >
              More insights
            </h2>
            <Link
              href="/blog"
              className="inline-flex items-center gap-2 text-sm font-semibold text-accent hover:text-accent-strong"
            >
              Back to all posts
              <ArrowRightIcon className="h-4 w-4" />
            </Link>
          </div>
          <div className="mt-8 grid gap-6 sm:grid-cols-2">
            {moreInsights.map((next) => (
              <article key={next.slug} className="card-surface">
                <Link
                  href={`/blog/${next.slug}`}
                  className="group flex h-full flex-col gap-3 p-6"
                >
                  <p className="font-mono text-xs text-ink-muted">
                    {formatDate(next.dateISO)} · {next.readingMinutes} min read
                  </p>
                  <h3 className="font-display text-lg font-semibold tracking-tight text-ink transition-colors group-hover:text-accent">
                    {next.title}
                  </h3>
                  <p className="line-clamp-2 text-sm leading-relaxed text-ink-soft">
                    {next.excerpt}
                  </p>
                </Link>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="border-t border-line py-16 sm:py-20">
        <div className="container-site">
          <div className="card-surface glow-accent mx-auto max-w-3xl p-8 text-center sm:p-12">
            <h2 className="font-display text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
              See what 100% call coverage looks like
            </h2>
            <p className="mx-auto mt-4 max-w-xl leading-relaxed text-ink-soft">
              Talk to our team about how Homayar&rsquo;s AI agents analyze,
              score, and coach across every customer conversation.
            </p>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-4">
              <ButtonLink href="/contact" variant="primary" size="lg">
                Book a demo
                <ArrowRightIcon className="h-4 w-4" />
              </ButtonLink>
              <ButtonLink href="/platform" variant="secondary" size="lg">
                Explore the platform
              </ButtonLink>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
