import type { Metadata } from "next";
import Link from "next/link";
import { getCaseStudies } from "@/lib/catalog";
import { Badge, ButtonLink, PageHero } from "@/components/ui";
import { ArrowRightIcon } from "@/components/icons";
import { absoluteUrl } from "@/lib/site";

export const metadata: Metadata = {
  title: "Case Studies | Homayar",
  description:
    "How call centers in banking, healthcare, telecom, insurance, and retail use Homayar to reach 100% QA coverage, cut handle time, and prove measurable results.",
  alternates: { canonical: absoluteUrl("/case-studies") },
};

const AVATAR_GRADIENTS = [
  "from-accent/25 to-violet/20 text-accent",
  "from-violet/25 to-accent/20 text-violet-soft",
  "from-teal/25 to-accent/20 text-teal",
  "from-amber/25 to-violet/20 text-amber",
  "from-accent/25 to-teal/20 text-accent",
] as const;

export default async function CaseStudiesPage() {
  const studies = await getCaseStudies();

  return (
    <>
      <PageHero
        eyebrow="Case studies"
        title="Measured results from teams that analyze every call"
        description="No vague success stories. Each case study below documents the operational problem, the deployment, and the metrics that moved — from QA coverage to handle time to regulator complaints."
      />

      <section className="container-site py-16 sm:py-20">
        <h2 className="sr-only">Customer case studies</h2>
        <div className="grid gap-6 md:grid-cols-2">
          {studies.map((study, i) => {
            const gradient = AVATAR_GRADIENTS[i % AVATAR_GRADIENTS.length];
            return (
              <article
                key={study.slug}
                className="card-surface flex flex-col p-6 sm:p-8"
              >
                <div className="flex items-start gap-4">
                  <div
                    aria-hidden
                    className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-xl border border-line bg-gradient-to-br font-display text-lg font-semibold tracking-tight ${gradient}`}
                  >
                    {study.logoInitials}
                  </div>
                  <div className="min-w-0">
                    <p className="font-display text-lg font-semibold tracking-tight text-ink">
                      {study.company}
                    </p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-2">
                      <Badge tone="accent">
                        <span className="capitalize">{study.industry}</span>
                      </Badge>
                      <Badge tone="neutral">{study.teamSize}</Badge>
                    </div>
                  </div>
                </div>

                <h3 className="mt-5 font-display text-xl font-semibold leading-snug tracking-tight text-ink">
                  <Link
                    href={`/case-studies/${study.slug}`}
                    className="transition-colors hover:text-accent"
                  >
                    {study.headline}
                  </Link>
                </h3>

                <p className="mt-3 text-sm leading-relaxed text-ink-soft">
                  {study.summary}
                </p>

                <dl className="mt-6 grid grid-cols-2 gap-3">
                  {study.results.slice(0, 2).map((result) => (
                    <div
                      key={result.metric}
                      className="rounded-lg border border-line bg-raised px-4 py-3"
                    >
                      <dd className="font-mono text-lg font-semibold text-accent">
                        {result.value}
                      </dd>
                      <dt className="mt-1 text-xs text-ink-muted">
                        {result.metric}
                      </dt>
                    </div>
                  ))}
                </dl>

                <div className="mt-6 flex flex-1 items-end">
                  <Link
                    href={`/case-studies/${study.slug}`}
                    className="inline-flex items-center gap-2 text-sm font-semibold text-accent transition-colors hover:text-accent-strong"
                  >
                    Read story
                    <ArrowRightIcon width={16} height={16} />
                  </Link>
                </div>
              </article>
            );
          })}
        </div>
      </section>

      <section className="border-t border-line bg-surface">
        <div className="container-site py-16 sm:py-20">
          <div className="card-surface flex flex-col items-start gap-6 p-8 sm:p-12 md:flex-row md:items-center md:justify-between">
            <div className="max-w-2xl">
              <h2 className="font-display text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
                Your numbers could be next
              </h2>
              <p className="mt-3 text-base leading-relaxed text-ink-soft">
                Every engagement starts with a baseline of your current QA
                coverage and call metrics — so the improvement is measured, not
                claimed.
              </p>
            </div>
            <ButtonLink href="/contact" variant="primary" size="lg">
              Book a demo
              <ArrowRightIcon width={18} height={18} />
            </ButtonLink>
          </div>
        </div>
      </section>
    </>
  );
}
