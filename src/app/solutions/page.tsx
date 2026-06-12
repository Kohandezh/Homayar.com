import type { Metadata } from "next";
import siteCopy from "@content/site-copy.json";
import { ButtonLink, PageHero, SectionHeading } from "@/components/ui";
import { ArrowRightIcon, CheckIcon, CategoryIcon } from "@/components/icons";

const { solutions } = siteCopy;

export const metadata: Metadata = {
  title: "Solutions by Industry & Team — Homayar",
  description:
    "How Homayar's agentic AI voice analysis adapts to banking, healthcare, insurance, telecom, retail, and government call centers — and to the QA, operations, sales, and compliance teams inside them.",
};

/**
 * Per-industry visual accent. Subtle: icon tint, top hairline, and
 * check-mark color only — body text stays on the standard ink scale.
 */
const INDUSTRY_STYLES: Record<
  string,
  { icon: string; text: string; iconBox: string; hairline: string }
> = {
  banking: {
    icon: "shield",
    text: "text-accent",
    iconBox: "border-accent/30 bg-accent/10",
    hairline: "via-accent/60",
  },
  healthcare: {
    icon: "heart",
    text: "text-teal",
    iconBox: "border-teal/30 bg-teal/10",
    hairline: "via-teal/60",
  },
  insurance: {
    icon: "gauge",
    text: "text-violet-soft",
    iconBox: "border-violet/30 bg-violet/10",
    hairline: "via-violet/60",
  },
  telecom: {
    icon: "waveform",
    text: "text-amber",
    iconBox: "border-amber/30 bg-amber/10",
    hairline: "via-amber/60",
  },
  retail: {
    icon: "trend",
    text: "text-success",
    iconBox: "border-success/30 bg-success/10",
    hairline: "via-success/60",
  },
  government: {
    icon: "server",
    text: "text-accent",
    iconBox: "border-accent/30 bg-accent/10",
    hairline: "via-accent/60",
  },
};

const TEAM_ICONS = ["gauge", "layers", "trend", "shield"];

export default function SolutionsPage() {
  return (
    <>
      <PageHero
        eyebrow="Solutions"
        title={solutions.heroH1}
        description={solutions.heroSub}
      >
        <div className="flex flex-wrap gap-3">
          <ButtonLink href="/contact" size="lg">
            Book a demo
            <ArrowRightIcon width={18} height={18} />
          </ButtonLink>
          <ButtonLink href="/platform" variant="secondary" size="lg">
            Explore the platform
          </ButtonLink>
        </div>
      </PageHero>

      {/* ── Industries ── */}
      <section aria-labelledby="industries-heading" className="py-16 sm:py-24">
        <div className="container-site">
          <SectionHeading
            eyebrow="By industry"
            title="Tuned to your sector's rules and vocabulary"
            description="Each deployment starts from an industry baseline — the regulations, call types, and quality criteria your sector already works to — then calibrates against your own calls."
          />
          {/* Visually hidden anchor for the aria-labelledby landmark */}
          <h2 id="industries-heading" className="sr-only">
            Industries Homayar serves
          </h2>

          <div className="mt-12 grid gap-6 md:grid-cols-2">
            {solutions.industries.map((industry) => {
              const style =
                INDUSTRY_STYLES[industry.slug] ?? INDUSTRY_STYLES.banking;
              return (
                <article
                  key={industry.slug}
                  className="card-surface relative flex flex-col overflow-hidden p-6 sm:p-8"
                >
                  <div
                    aria-hidden
                    className={`absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent to-transparent ${style.hairline}`}
                  />
                  <div className="flex items-center gap-3">
                    <span
                      className={`inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border ${style.iconBox} ${style.text}`}
                    >
                      <CategoryIcon icon={style.icon} width={20} height={20} />
                    </span>
                    <p
                      className={`text-xs font-semibold uppercase tracking-[0.16em] ${style.text}`}
                    >
                      {industry.name}
                    </p>
                  </div>

                  <h3 className="mt-5 font-display text-xl font-semibold tracking-tight text-ink sm:text-2xl">
                    {industry.headline}
                  </h3>
                  <p className="mt-3 text-sm leading-relaxed text-ink-soft sm:text-base">
                    {industry.description}
                  </p>

                  <ul className="mt-6 space-y-3 border-t border-line pt-6">
                    {industry.outcomes.map((outcome) => (
                      <li key={outcome} className="flex items-start gap-3">
                        <CheckIcon
                          width={16}
                          height={16}
                          className={`mt-0.5 shrink-0 ${style.text}`}
                        />
                        <span className="text-sm leading-relaxed text-ink-soft">
                          {outcome}
                        </span>
                      </li>
                    ))}
                  </ul>
                </article>
              );
            })}
          </div>
        </div>
      </section>

      {/* ── Teams ── */}
      <section
        aria-labelledby="teams-heading"
        className="border-t border-line bg-surface py-16 sm:py-24"
      >
        <div className="container-site">
          <SectionHeading
            eyebrow="By team"
            title="One platform, four ways to win with it"
            description="The same scored, evidence-backed call data serves every team that depends on conversation quality — each with its own view of the results."
          />
          <h2 id="teams-heading" className="sr-only">
            Teams Homayar serves
          </h2>

          <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {solutions.teams.map((team, i) => (
              <article
                key={team.name}
                className="card-surface flex flex-col p-6"
              >
                <span className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-line-strong bg-raised text-accent">
                  <CategoryIcon
                    icon={TEAM_ICONS[i] ?? "layers"}
                    width={20}
                    height={20}
                  />
                </span>
                <p className="mt-5 text-xs font-semibold uppercase tracking-[0.16em] text-ink-muted">
                  {team.name}
                </p>
                <h3 className="mt-2 font-display text-lg font-semibold leading-snug tracking-tight text-ink">
                  {team.headline}
                </h3>
                <p className="mt-3 text-sm leading-relaxed text-ink-soft">
                  {team.description}
                </p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* ── CTA band ── */}
      <section className="border-t border-line py-16 sm:py-20">
        <div className="container-site">
          <div className="card-surface glow-accent flex flex-col items-start gap-6 p-8 sm:p-12 md:flex-row md:items-center md:justify-between">
            <div className="max-w-xl">
              <h2 className="font-display text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
                See Homayar on your industry&apos;s calls
              </h2>
              <p className="mt-3 leading-relaxed text-ink-soft">
                Bring a sample of your own recordings to the demo. We&apos;ll
                run them through the agent team and walk through the scores,
                signals, and evidence together.
              </p>
            </div>
            <div className="flex flex-wrap gap-3">
              <ButtonLink href="/contact" size="lg">
                Talk to our team
                <ArrowRightIcon width={18} height={18} />
              </ButtonLink>
              <ButtonLink href="/case-studies" variant="secondary" size="lg">
                Read case studies
              </ButtonLink>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
