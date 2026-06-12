import type { Metadata } from "next";
import { CheckIcon } from "@/components/icons";
import { ButtonLink } from "@/components/ui";
import { CartClearer } from "@/components/cart-clearer";
import { getStripe, hasStripe } from "@/lib/stripe";

export const metadata: Metadata = {
  title: "Order confirmed",
  description: "Your Homayar order was completed successfully.",
  robots: { index: false },
};

async function getSessionSummary(sessionId: string | undefined) {
  if (!sessionId || !hasStripe()) return null;
  try {
    const session = await getStripe().checkout.sessions.retrieve(sessionId);
    return {
      email: session.customer_details?.email ?? null,
      amountTotal: session.amount_total,
      currency: session.currency ?? "usd",
      status: session.status,
    };
  } catch {
    return null;
  }
}

export default async function SuccessPage({
  searchParams,
}: {
  searchParams: Promise<{ session_id?: string }>;
}) {
  const { session_id } = await searchParams;
  const summary = await getSessionSummary(session_id);

  return (
    <div className="container-site flex flex-col items-center py-24 text-center">
      <CartClearer />
      <div className="flex h-16 w-16 items-center justify-center rounded-full border border-success/40 bg-success/10 text-success">
        <CheckIcon width={30} height={30} />
      </div>
      <h1 className="mt-6 font-display text-4xl font-semibold tracking-tight text-ink">
        Thank you — order confirmed
      </h1>
      <p className="mt-4 max-w-lg text-lg text-ink-muted">
        {summary?.email
          ? `A receipt is on its way to ${summary.email}.`
          : "Your test-mode payment completed successfully."}{" "}
        Our team will reach out within one business day to start onboarding.
      </p>

      {summary?.amountTotal != null && (
        <p className="mt-4 rounded-xl border border-line bg-raised px-5 py-3 font-mono text-sm text-ink-soft">
          Paid: $
          {(summary.amountTotal / 100).toLocaleString("en-US")}{" "}
          {summary.currency.toUpperCase()} · Stripe test mode
        </p>
      )}

      <div className="mt-10 grid w-full max-w-2xl gap-4 text-left sm:grid-cols-3">
        {[
          {
            step: "1",
            title: "Kickoff call",
            text: "We map your telephony stack and goals.",
          },
          {
            step: "2",
            title: "Connect calls",
            text: "Secure ingestion from your CCaaS or uploads.",
          },
          {
            step: "3",
            title: "First insights",
            text: "Scored calls and dashboards within days.",
          },
        ].map((s) => (
          <div key={s.step} className="card-surface p-5">
            <p className="font-mono text-xs text-accent">Step {s.step}</p>
            <p className="mt-2 font-medium text-ink">{s.title}</p>
            <p className="mt-1 text-sm text-ink-muted">{s.text}</p>
          </div>
        ))}
      </div>

      <div className="mt-10 flex gap-3">
        <ButtonLink href="/catalog" variant="secondary">
          Continue browsing
        </ButtonLink>
        <ButtonLink href="/contact">Talk to the team</ButtonLink>
      </div>
    </div>
  );
}
