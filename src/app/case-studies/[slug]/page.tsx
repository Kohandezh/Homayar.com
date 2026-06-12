import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getCaseStudies, getCaseStudyBySlug } from "@/lib/catalog";
import { Badge, ButtonLink, SectionHeading } from "@/components/ui";
import { ArrowRightIcon } from "@/components/icons";
import { absoluteUrl } from "@/lib/site";

const AVATAR_GRADIENTS = [
  "from-accent/25 to-violet/20 text-accent",
  "from-violet/25 to-accent/20 text-violet-soft",
  "from-teal/25 to-accent/20 text-teal",
  "from-amber/25 to-violet/20 text-amber",
  "from-accent/25 to-teal/20 text-accent",
] as const;

export async function generateStaticParams() {
  const studies = await getCaseStudies();
  return studies.map((study) => ({ slug: study.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const study = await getCaseStudyBySlug(slug);
  if (!study) return { title: "Case study not found | Homayar" };
  return {
    title: study.seoTitle,
    description: study.seoDescription,
    alternates: { canonical: absoluteUrl(`/case-studies/${study.slug}`) },
    openGraph: {
      title: study.seoTitle,
      description: study.seoDescription,
      url: absoluteUrl(`/case-studies/${study.slug}`),
      type: "article",
    },
  };
}

export default async function CaseStudyPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const studies = await getCaseStudies();
  const index = studies.findIndex((s) => s.slug === slug);
  if (index === -1) notFound();

  const study = studies[index];
  const nextStudy = studies[(index + 1) % studies.length];
  const gradient = AVATAR_GRADIENTS[index % AVATAR_GRADIENTS.length];

  return (
    <article>
      {/* Header */}
      <header className="relative overflow-hidden border-b border-line">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(60% 80% at 70% 0%, rgba(34,211,238,0.10), transparent 60%), radial-gradient(50% 70% at 20% 10%, rgba(139,92,246,0.10), transparent 60%)",
          }}
        />
        <div className="container-site relative py-16 sm:py-20">
          <nav aria-label="Breadcrumb" className="mb-8">
            <Link
              href="/case-studies"
              className="text-sm font-medium text-ink-muted transition-colors hover:text-accent"
            >
              ← All case studies
            </Link>
          </nav>
          <div className="flex flex-col gap-6 sm:flex-row sm:items-center">
            <div
              aria-hidden
              className={`flex h-16 w-16 shrink-0 items-center justify-center rounded-2xl border border-line bg-gradient-to-br font-display text-xl font-semibold tracking-tight ${gradient}`}
            >
              {study.logoInitials}
            </div>
            <div>
              <p className="font-display text-xl font-semibold tracking-tight text-ink">
                {study.company}
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Badge tone="accent">
                  <span className="capitalize">{study.industry}</span>
                </Badge>
                <Badge tone="neutral">{study.teamSize}</Badge>
              </div>
            </div>
          </div>
          <h1 className="mt-8 max-w-4xl font-display text-3xl font-semibold leading-tight tracking-tight text-ink sm:text-4xl lg:text-5xl">
            {study.headline}
          </h1>
          <p className="mt-6 max-w-3xl text-lg leading-relaxed text-ink-soft">
            {study.summary}
          </p>
        </div>
      </header>

      {/* Results band */}
      <section
        aria-labelledby="results-heading"
        className="border-b border-line bg-surface"
      >
        <div className="container-site py-12 sm:py-16">
          <h2 id="results-heading" className="sr-only">
            Results
          </h2>
          <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {study.results.map((result) => (
              <div
                key={result.metric}
                className="card-surface flex flex-col justify-between p-6"
              >
                <dd className="font-mono text-3xl font-semibold tracking-tight text-accent">
                  {result.value}
                </dd>
                <dt className="mt-3 text-sm leading-snug text-ink-muted">
                  {result.metric}
                </dt>
              </div>
            ))}
          </dl>
        </div>
      </section>

      {/* Challenge */}
      <section className="container-site py-16 sm:py-20">
        <div className="grid gap-10 lg:grid-cols-3">
          <SectionHeading eyebrow="The starting point" title="The challenge" />
          <div className="lg:col-span-2">
            <p className="text-base leading-relaxed text-ink-soft sm:text-lg">
              {study.challenge}
            </p>
          </div>
        </div>
      </section>

      {/* Solution */}
      <section className="border-t border-line bg-surface">
        <div className="container-site py-16 sm:py-20">
          <div className="grid gap-10 lg:grid-cols-3">
            <SectionHeading eyebrow="The deployment" title="The solution" />
            <div className="lg:col-span-2">
              <p className="text-base leading-relaxed text-ink-soft sm:text-lg">
                {study.solution}
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Pull quote */}
      <section className="container-site py-16 sm:py-20">
        <figure className="mx-auto max-w-3xl border-l-4 border-accent pl-6 sm:pl-10">
          <blockquote className="font-display text-xl font-medium leading-relaxed tracking-tight text-ink sm:text-2xl">
            &ldquo;{study.quote}&rdquo;
          </blockquote>
          <figcaption className="mt-5 text-sm text-ink-muted">
            {study.quoteAuthor}
          </figcaption>
        </figure>
      </section>

      {/* Footer nav */}
      <footer className="border-t border-line bg-surface">
        <div className="container-site py-12 sm:py-16">
          <div className="flex flex-col gap-8 md:flex-row md:items-center md:justify-between">
            <div>
              <p className="text-sm font-semibold uppercase tracking-[0.18em] text-ink-muted">
                Next case study
              </p>
              <Link
                href={`/case-studies/${nextStudy.slug}`}
                className="group mt-3 inline-flex items-center gap-3"
              >
                <span className="font-display text-xl font-semibold tracking-tight text-ink transition-colors group-hover:text-accent">
                  {nextStudy.company}
                </span>
                <ArrowRightIcon
                  width={20}
                  height={20}
                  className="text-accent transition-transform group-hover:translate-x-1"
                />
              </Link>
              <p className="mt-2 max-w-xl text-sm text-ink-muted">
                {nextStudy.headline}
              </p>
            </div>
            <ButtonLink href="/contact" variant="primary" size="lg">
              Get results like these
              <ArrowRightIcon width={18} height={18} />
            </ButtonLink>
          </div>
        </div>
      </footer>
    </article>
  );
}
