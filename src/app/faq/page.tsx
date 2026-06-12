import type { Metadata } from "next";
import { getFaq } from "@/lib/catalog";
import { ButtonLink, PageHero, SectionHeading } from "@/components/ui";
import { ChevronDownIcon } from "@/components/icons";
import type { FaqItem } from "@/lib/types";

export const metadata: Metadata = {
  title: "FAQ — Frequently asked questions",
  description:
    "Straight answers about Homayar: how the AI agents analyze calls, language support, accuracy, data retention, PII redaction, pricing, deployment options, and integrations.",
};

const GROUP_ORDER = [
  "Product",
  "Security & Privacy",
  "Pricing & Billing",
  "Deployment & Integration",
] as const;

const GROUP_EYEBROWS: Record<string, string> = {
  Product: "Product",
  "Security & Privacy": "Security",
  "Pricing & Billing": "Pricing",
  "Deployment & Integration": "Deployment",
};

function FaqAccordion({ item }: { item: FaqItem }) {
  return (
    <details className="group rounded-xl border border-line bg-surface">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 rounded-xl px-5 py-4 text-left font-medium text-ink transition-colors hover:text-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden">
        {item.question}
        <ChevronDownIcon className="h-5 w-5 shrink-0 text-ink-muted transition-transform duration-200 group-open:rotate-180" />
      </summary>
      <p className="px-5 pb-5 leading-relaxed text-ink-soft">{item.answer}</p>
    </details>
  );
}

export default function FaqPage() {
  const faq = getFaq();
  const groups = GROUP_ORDER.map((group) => ({
    group,
    items: faq.filter((item) => item.group === group),
  })).filter(({ items }) => items.length > 0);

  return (
    <>
      <PageHero
        eyebrow="FAQ"
        title="Frequently asked questions"
        description="Straight answers on how Homayar analyzes calls, protects your data, prices by volume, and deploys alongside your stack."
      />

      <section className="py-16 sm:py-24">
        <div className="container-site">
          <div className="space-y-16">
            {groups.map(({ group, items }) => (
              <div key={group}>
                <SectionHeading eyebrow={GROUP_EYEBROWS[group]} title={group} />
                <div className="mt-8 space-y-3">
                  {items.map((item) => (
                    <FaqAccordion key={item.question} item={item} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Still have questions */}
      <section className="border-t border-line py-16 sm:py-24">
        <div className="container-site">
          <div className="mx-auto max-w-2xl text-center">
            <h2 className="font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
              Still have questions?
            </h2>
            <p className="mt-4 text-lg leading-relaxed text-ink-soft">
              Talk to the team directly — we can walk through your call
              volumes, security requirements, and rollout plan in a 30-minute
              session.
            </p>
            <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <ButtonLink href="/contact" variant="primary" size="lg">
                Request a demo
              </ButtonLink>
              <a
                href="mailto:info@ksf.ir"
                className="inline-flex h-12 items-center justify-center gap-2 rounded-xl border border-line-strong px-6 text-base text-ink transition-colors hover:border-accent hover:text-accent"
              >
                Email info@ksf.ir
              </a>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
