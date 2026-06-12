import type { Metadata } from "next";
import siteCopy from "@content/site-copy.json";
import { PageHero } from "@/components/ui";
import { CheckIcon } from "@/components/icons";
import { ContactForm } from "@/components/contact-form";

const contact = siteCopy.contact;

export const metadata: Metadata = {
  title: "Contact — Request a demo",
  description:
    "Book a 30-minute Homayar walkthrough. Bring a sample call recording and leave with a scored, agent-analyzed report — no commitment, no setup required.",
};

const EXPECTATIONS = [
  {
    title: "A 30-minute walkthrough",
    description:
      "A focused session with the Homayar team — how the agent pipeline works, what the dashboards show, and how scoring maps to your existing QA rubric.",
  },
  {
    title: "Your own calls analyzed",
    description:
      "Bring a sample recording and watch it transcribed, scored, and flagged live. You leave with a real report from your own conversation, not a canned demo.",
  },
  {
    title: "A tailored rollout plan",
    description:
      "Based on your call volume, telephony stack, and compliance requirements, we outline a concrete pilot scope, timeline, and success criteria for your team.",
  },
];

function AlternativeNote({ note }: { note: string }) {
  const email = "info@ksf.ir";
  const index = note.indexOf(email);
  if (index === -1) {
    return <p className="text-sm leading-relaxed text-ink-muted">{note}</p>;
  }
  return (
    <p className="text-sm leading-relaxed text-ink-muted">
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

export default function ContactPage() {
  return (
    <>
      <PageHero
        eyebrow="Contact"
        title={contact.heroH1}
        description={contact.heroSub}
      />

      <section className="py-16 sm:py-24">
        <div className="container-site">
          <div className="grid gap-12 lg:grid-cols-2 lg:gap-16">
            {/* What to expect */}
            <div>
              <h2 className="font-display text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
                What to expect
              </h2>
              <ul className="mt-8 space-y-6">
                {EXPECTATIONS.map((item) => (
                  <li key={item.title} className="flex gap-4">
                    <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-accent/40 bg-accent/10 text-accent">
                      <CheckIcon className="h-4 w-4" />
                    </span>
                    <div>
                      <h3 className="font-medium text-ink">{item.title}</h3>
                      <p className="mt-1.5 leading-relaxed text-ink-soft">
                        {item.description}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
              <div className="mt-10 rounded-xl border border-line bg-surface p-5">
                <AlternativeNote note={contact.alternativeNote} />
              </div>
            </div>

            {/* Form */}
            <div>
              <ContactForm
                labels={contact.formLabels}
                successMessage={contact.successMessage}
              />
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
