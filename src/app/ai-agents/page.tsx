import type { Metadata } from "next";
import siteCopy from "@content/site-copy.json";
import { ButtonLink, PageHero, SectionHeading } from "@/components/ui";
import { ArrowRightIcon, CheckIcon } from "@/components/icons";
import { AgentGlyph } from "@/components/platform/agent-glyphs";
import { OrchestrationDiagram } from "@/components/platform/orchestration-diagram";

const copy = siteCopy.aiAgents;

export const metadata: Metadata = {
  title: "AI Agents",
  description:
    "Meet Homayar's seven specialized AI agents — Listener, Scribe, Judge, Guardian, Scout, Coach, and Analyst — that collaborate on every call, each citing transcript evidence for its findings.",
};

const TONES = [
  {
    text: "text-accent",
    badge: "border-accent/30 bg-accent/10",
    check: "text-accent",
  },
  {
    text: "text-violet-soft",
    badge: "border-violet/30 bg-violet/10",
    check: "text-violet-soft",
  },
  {
    text: "text-amber",
    badge: "border-amber/30 bg-amber/10",
    check: "text-amber",
  },
  {
    text: "text-teal",
    badge: "border-teal/30 bg-teal/10",
    check: "text-teal",
  },
] as const;

export default function AiAgentsPage() {
  return (
    <>
      <PageHero
        eyebrow="AI Agents"
        title={copy.heroH1}
        description={copy.heroSub}
      >
        <div className="flex flex-wrap gap-3">
          <ButtonLink href="/contact" size="lg">
            Request a demo
            <ArrowRightIcon width={18} height={18} />
          </ButtonLink>
          <ButtonLink href="/platform" variant="secondary" size="lg">
            See the pipeline
          </ButtonLink>
        </div>
      </PageHero>

      {/* ── Agent roster ── */}
      <section aria-labelledby="agents-heading" className="py-16 sm:py-24">
        <div className="container-site">
          <SectionHeading
            eyebrow="The roster"
            title="Seven specialists, one standard of evidence"
            description="Each agent has a defined role and a defined output. None of them summarizes in the abstract — every finding links back to the transcript moment that supports it."
          />
          <h2 id="agents-heading" className="sr-only">
            The seven Homayar AI agents
          </h2>

          <div className="mt-12 grid gap-6 md:grid-cols-2 xl:grid-cols-3">
            {copy.agents.map((agent, i) => {
              const tone = TONES[i % TONES.length];
              return (
                <article
                  key={agent.name}
                  className="card-surface flex flex-col rounded-xl p-6"
                >
                  <div className="flex items-center gap-4">
                    <div
                      className={`inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-lg border ${tone.badge} ${tone.text}`}
                    >
                      <AgentGlyph name={agent.name} />
                    </div>
                    <div>
                      <h3 className="font-display text-xl font-semibold tracking-tight text-ink">
                        {agent.name}
                      </h3>
                      <p
                        className={`text-xs font-semibold uppercase tracking-[0.14em] ${tone.text}`}
                      >
                        {agent.role}
                      </p>
                    </div>
                  </div>

                  <p className="mt-4 text-sm leading-relaxed text-ink-soft">
                    {agent.description}
                  </p>

                  <ul className="mt-5 space-y-2.5 border-t border-line pt-5">
                    {agent.capabilities.map((capability) => (
                      <li
                        key={capability}
                        className="flex items-start gap-2.5 text-sm leading-snug text-ink-soft"
                      >
                        <CheckIcon
                          width={16}
                          height={16}
                          className={`mt-0.5 shrink-0 ${tone.check}`}
                        />
                        {capability}
                      </li>
                    ))}
                  </ul>
                </article>
              );
            })}
          </div>
        </div>
      </section>

      {/* ── Orchestration ── */}
      <section
        aria-labelledby="orchestration-heading"
        className="border-y border-line bg-surface py-16 sm:py-24"
      >
        <div className="container-site">
          <div className="grid items-center gap-12 lg:grid-cols-2 lg:gap-16">
            <div>
              <SectionHeading
                eyebrow="Orchestration"
                title={copy.orchestration.title}
                description={copy.orchestration.description}
              />
              <h2 id="orchestration-heading" className="sr-only">
                How the agents collaborate on a call
              </h2>
            </div>
            <OrchestrationDiagram />
          </div>
        </div>
      </section>

      {/* ── CTA band ── */}
      <section
        aria-labelledby="agents-cta-heading"
        className="py-16 sm:py-20"
      >
        <div className="container-site">
          <div className="mx-auto max-w-2xl text-center">
            <h2
              id="agents-cta-heading"
              className="font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl"
            >
              Put the team on your next call
            </h2>
            <p className="mt-4 text-lg leading-relaxed text-ink-muted">
              Bring a sample recording to a 30-minute walkthrough and watch all
              seven agents work it — scores, flags, and coaching notes, with
              evidence attached.
            </p>
            <div className="mt-8 flex justify-center">
              <ButtonLink href="/contact" size="lg">
                Request a demo
                <ArrowRightIcon width={18} height={18} />
              </ButtonLink>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
