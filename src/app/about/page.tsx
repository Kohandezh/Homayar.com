import type { Metadata } from "next";
import { aboutContent } from "@/lib/catalog";
import { ButtonLink, Eyebrow, PageHero, SectionHeading } from "@/components/ui";

export const metadata: Metadata = {
  title: "About — Every call has a companion",
  description:
    "Why Homayar exists, the Persian story behind the name, the values that shape the product, and the KSF Company team building agentic AI voice analysis for call centers.",
};

function CompanyNote({ note }: { note: string }) {
  const email = "info@ksf.ir";
  const index = note.indexOf(email);
  if (index === -1) {
    return <p className="leading-relaxed text-ink-soft">{note}</p>;
  }
  return (
    <p className="leading-relaxed text-ink-soft">
      {note.slice(0, index)}
      <a
        href={`mailto:${email}`}
        className="font-medium text-accent underline underline-offset-4 hover:text-accent-strong"
      >
        {email}
      </a>
      {note.slice(index + email.length)}
    </p>
  );
}

export default function AboutPage() {
  const paragraphs = aboutContent.missionBody.split("\n\n");
  const [missionLead, ...missionRest] = paragraphs;

  return (
    <>
      <PageHero
        eyebrow="About Homayar"
        title={aboutContent.missionHeadline}
        description={missionLead}
      />

      {/* Mission */}
      <section className="border-b border-line py-16 sm:py-24">
        <div className="container-site">
          <SectionHeading
            eyebrow="Our mission"
            title="A companion, not a camera"
          />
          <div className="mt-8 max-w-3xl space-y-6">
            {missionRest.map((paragraph) => (
              <p
                key={paragraph.slice(0, 40)}
                className="text-lg leading-relaxed text-ink-soft"
              >
                {paragraph}
              </p>
            ))}
          </div>
        </div>
      </section>

      {/* The name */}
      <section className="border-b border-line py-16 sm:py-24">
        <div className="container-site">
          <figure className="relative overflow-hidden rounded-2xl border border-line bg-surface px-6 py-12 sm:px-12 sm:py-16">
            <span
              aria-hidden
              className="pointer-events-none absolute -top-6 right-2 select-none font-display text-[6rem] font-semibold leading-none text-accent/10 sm:-top-10 sm:text-[11rem]"
            >
              همایار
            </span>
            <div className="relative">
              <Eyebrow>The name</Eyebrow>
              <blockquote className="max-w-3xl font-display text-xl leading-relaxed tracking-tight text-ink sm:text-2xl">
                {aboutContent.nameStory}
              </blockquote>
              <figcaption className="mt-6 flex items-center gap-3 text-sm text-ink-muted">
                <span className="font-mono text-accent">Homa</span>
                <span aria-hidden>+</span>
                <span className="font-mono text-accent">Yar</span>
                <span aria-hidden className="h-px w-8 bg-line-strong" />
                <span>the fortune-bird and the companion</span>
              </figcaption>
            </div>
          </figure>
        </div>
      </section>

      {/* Values */}
      <section className="border-b border-line py-16 sm:py-24">
        <div className="container-site">
          <SectionHeading
            eyebrow="What we stand for"
            title="The values behind every product decision"
          />
          <div className="mt-10 grid gap-6 sm:grid-cols-2">
            {aboutContent.values.map((value, index) => (
              <article key={value.name} className="card-surface rounded-2xl p-6 sm:p-8">
                <p className="font-mono text-sm text-accent">
                  {String(index + 1).padStart(2, "0")}
                </p>
                <h3 className="mt-3 font-display text-xl font-semibold tracking-tight text-ink">
                  {value.name}
                </h3>
                <p className="mt-3 leading-relaxed text-ink-soft">
                  {value.description}
                </p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* Company note */}
      <section className="border-b border-line py-16 sm:py-24">
        <div className="container-site">
          <div className="grid gap-10 lg:grid-cols-[1fr_2fr] lg:gap-16">
            <SectionHeading eyebrow="The company" title="Built by KSF Company" />
            <div className="max-w-3xl lg:pt-2">
              <CompanyNote note={aboutContent.companyNote} />
            </div>
          </div>
        </div>
      </section>

      {/* CTA band */}
      <section className="py-16 sm:py-24">
        <div className="container-site">
          <div className="relative overflow-hidden rounded-2xl border border-line bg-surface px-6 py-12 text-center sm:px-12 sm:py-16">
            <div
              aria-hidden
              className="pointer-events-none absolute inset-0"
              style={{
                background:
                  "radial-gradient(50% 80% at 50% 0%, rgba(34,211,238,0.12), transparent 70%)",
              }}
            />
            <div className="relative mx-auto max-w-2xl">
              <h2 className="font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
                Put a companion on every call
              </h2>
              <p className="mt-4 text-lg leading-relaxed text-ink-soft">
                Book a 30-minute walkthrough with the team, or see the results
                enterprise call centers have measured with Homayar.
              </p>
              <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
                <ButtonLink href="/contact" variant="primary" size="lg">
                  Request a demo
                </ButtonLink>
                <ButtonLink href="/case-studies" variant="secondary" size="lg">
                  Read case studies
                </ButtonLink>
              </div>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
