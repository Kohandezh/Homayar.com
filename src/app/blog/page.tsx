import type { Metadata } from "next";
import Link from "next/link";
import { getBlogPosts } from "@/lib/catalog";
import { Badge, PageHero } from "@/components/ui";
import { ArrowRightIcon } from "@/components/icons";
import { absoluteUrl } from "@/lib/site";

export const metadata: Metadata = {
  title: "Blog & Insights",
  description:
    "Research, engineering notes, and operational playbooks on agentic AI voice analysis for call centers — quality coverage, coaching, compliance, and churn signals.",
  alternates: { canonical: absoluteUrl("/blog") },
};

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

export default async function BlogIndexPage() {
  const posts = await getBlogPosts();
  const [featured, ...rest] = posts;

  return (
    <>
      <PageHero
        eyebrow="Blog & Insights"
        title="Notes from the floor of the AI-analyzed call center"
        description="Metric-led writing on quality coverage, agent coaching, compliance monitoring, and the engineering behind Homayar's agentic voice analysis."
      />

      {featured && (
        <section aria-labelledby="featured-post-heading" className="py-12 sm:py-16">
          <div className="container-site">
            <h2 id="featured-post-heading" className="sr-only">
              Featured post
            </h2>
            <article className="card-surface overflow-hidden">
              <Link
                href={`/blog/${featured.slug}`}
                className="group grid gap-8 p-6 sm:p-8 lg:grid-cols-[1fr_minmax(0,2fr)] lg:p-10"
              >
                <div className="flex flex-col justify-between gap-6 border-line lg:border-r lg:pr-8">
                  <div>
                    <p className="text-sm font-semibold uppercase tracking-[0.18em] text-accent">
                      Latest
                    </p>
                    <p className="mt-4 font-mono text-sm text-ink-muted">
                      {formatDate(featured.dateISO)}
                    </p>
                    <p className="mt-1 font-mono text-sm text-ink-muted">
                      {featured.readingMinutes} min read
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {featured.tags.map((tag) => (
                      <Badge key={tag} tone="accent">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                </div>
                <div className="flex flex-col justify-between gap-6">
                  <div>
                    <h3 className="font-display text-2xl font-semibold tracking-tight text-ink transition-colors group-hover:text-accent sm:text-3xl">
                      {featured.title}
                    </h3>
                    <p className="mt-4 max-w-2xl leading-relaxed text-ink-soft">
                      {featured.excerpt}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-4">
                    <p className="text-sm text-ink-muted">{featured.author}</p>
                    <span className="inline-flex items-center gap-2 text-sm font-semibold text-accent">
                      Read the post
                      <ArrowRightIcon className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
                    </span>
                  </div>
                </div>
              </Link>
            </article>
          </div>
        </section>
      )}

      {rest.length > 0 && (
        <section
          aria-labelledby="all-posts-heading"
          className="border-t border-line py-12 sm:py-16"
        >
          <div className="container-site">
            <h2
              id="all-posts-heading"
              className="font-display text-2xl font-semibold tracking-tight text-ink"
            >
              All posts
            </h2>
            <div className="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {rest.map((post) => (
                <article key={post.slug} className="card-surface">
                  <Link
                    href={`/blog/${post.slug}`}
                    className="group flex h-full flex-col gap-4 p-6"
                  >
                    <div className="flex flex-wrap gap-2">
                      {post.tags.slice(0, 2).map((tag) => (
                        <Badge key={tag} tone="neutral">
                          {tag}
                        </Badge>
                      ))}
                    </div>
                    <h3 className="font-display text-lg font-semibold tracking-tight text-ink transition-colors group-hover:text-accent">
                      {post.title}
                    </h3>
                    <p className="line-clamp-3 text-sm leading-relaxed text-ink-soft">
                      {post.excerpt}
                    </p>
                    <div className="mt-auto flex items-center justify-between gap-3 border-t border-line pt-4">
                      <p className="text-xs text-ink-muted">{post.author.split(",")[0]}</p>
                      <p className="font-mono text-xs text-ink-muted">
                        {formatDate(post.dateISO)} · {post.readingMinutes} min
                      </p>
                    </div>
                  </Link>
                </article>
              ))}
            </div>
          </div>
        </section>
      )}
    </>
  );
}
