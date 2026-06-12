import type { Metadata } from "next";
import siteCopy from "@content/site-copy.json";
import { ButtonLink, PageHero, SectionHeading } from "@/components/ui";
import { ArrowRightIcon, CategoryIcon } from "@/components/icons";
import { ArchitectureDiagram } from "@/components/platform/architecture-diagram";

const copy = siteCopy.platform;

export const metadata: Metadata = {
  title: "Platform",
  description:
    "How Homayar works: a six-step pipeline that ingests call recordings, transcribes them, runs multi-agent AI analysis, scores quality, surfaces insights, and delivers coaching — with enterprise-grade security.",
};

const SECURITY_ICONS = ["shield", "shield", "server", "server"] as const;

export default function PlatformPage() {
  return (
    <>
      <PageHero
        eyebrow="Platform"
        title={copy.heroH1}
        description={copy.heroSub}
      >
        <div className="flex flex-wrap gap-3">
          <ButtonLink href="/contact" size="lg">
            Request a demo
            <ArrowRightIcon width={18} height={18} />
          </ButtonLink>
          <ButtonLink href="/ai-agents" variant="secondary" size="lg">
            Meet the AI agents
          </ButtonLink>
        </div>
      </PageHero>

      {/* ── Pipeline ── */}
      <section aria-labelledby="pipeline-heading" className="py-16 sm:py-24">
        <div className="container-site">
          <SectionHeading
            eyebrow="The pipeline"
            title="Six steps from recording to result"
            description="Every call follows the same path — automatically, in minutes. Each stage hands verified output to the next, so nothing downstream is built on guesswork."
          />
          <h2 id="pipeline-heading" className="sr-only">
            The Homayar analysis pipeline
          </h2>

          <ol className="mt-12 max-w-3xl">
            {copy.pipeline.map((step, i) => (
              <li key={step.step} className="relative flex gap-5 sm:gap-7">
                {/* node + connector rail */}
                <div className="flex flex-col items-center">
                  <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full border border-accent/40 bg-accent/10 font-mono text-sm font-medium text-accent">
                    {step.step}
                  </div>
                  {i < copy.pipeline.length - 1 && (
                    <div
                      aria-hidden
                      className="w-px flex-1 border-l border-dashed border-line-strong"
                    />
                  )}
                </div>
                <div
                  className={`card-surface flex-1 rounded-xl p-5 sm:p-6 ${
                    i < copy.pipeline.length - 1 ? "mb-6" : ""
                  }`}
                >
                  <h3 className="font-display text-lg font-semibold tracking-tight text-ink">
                    {step.title}
                  </h3>
                  <p className="mt-2 text-sm leading-relaxed text-ink-soft">
                    {step.description}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* ── Security ── */}
      <section
        aria-labelledby="security-heading"
        className="border-y border-line bg-surface py-16 sm:py-24"
      >
        <div className="container-site">
          <SectionHeading eyebrow="Security" title={copy.security.title} />
          <h2 id="security-heading" className="sr-only">
            Security and deployment
          </h2>
          <div className="mt-12 grid gap-6 sm:grid-cols-2">
            {copy.security.items.map((item, i) => (
              <div key={item.title} className="card-surface rounded-xl p-6">
                <div className="mb-4 inline-flex h-11 w-11 items-center justify-center rounded-lg border border-accent/30 bg-accent/10 text-accent">
                  <CategoryIcon icon={SECURITY_ICONS[i % SECURITY_ICONS.length]} />
                </div>
                <h3 className="font-display text-lg font-semibold tracking-tight text-ink">
                  {item.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-ink-soft">
                  {item.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Architecture ── */}
      <section aria-labelledby="architecture-heading" className="py-16 sm:py-24">
        <div className="container-site">
          <div className="grid items-start gap-12 lg:grid-cols-2 lg:gap-16">
            <div>
              <SectionHeading
                eyebrow="Architecture"
                title={copy.architecture.title}
                description={copy.architecture.description}
              />
              <h2 id="architecture-heading" className="sr-only">
                Platform architecture
              </h2>
              <div className="mt-8">
                <ButtonLink href="/catalog" variant="secondary">
                  Browse platform modules
                  <ArrowRightIcon width={16} height={16} />
                </ButtonLink>
              </div>
            </div>
            <ArchitectureDiagram />
          </div>
        </div>
      </section>

      {/* ── CTA band ── */}
      <section
        aria-labelledby="platform-cta-heading"
        className="border-t border-line bg-surface py-16 sm:py-20"
      >
        <div className="container-site">
          <div className="mx-auto max-w-2xl text-center">
            <h2
              id="platform-cta-heading"
              className="font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl"
            >
              See the pipeline run on your own calls
            </h2>
            <p className="mt-4 text-lg leading-relaxed text-ink-muted">
              Bring a recording to a 30-minute walkthrough and leave with a
              scored, agent-analyzed report — or explore the modules that make
              up the platform.
            </p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <ButtonLink href="/contact" size="lg">
                Request a demo
                <ArrowRightIcon width={18} height={18} />
              </ButtonLink>
              <ButtonLink href="/catalog" variant="secondary" size="lg">
                Explore the catalog
              </ButtonLink>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
