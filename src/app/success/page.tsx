import type { Metadata } from "next";
import { getStripe, hasStripe } from "@/lib/stripe";
import { SuccessContent, type SuccessSummary } from "./content";

export const metadata: Metadata = {
  title: "سفارش تأیید شد — همایار",
  description: "سفارش شما در همایار با موفقیت تکمیل شد.",
  robots: { index: false },
};

async function getSessionSummary(
  sessionId: string | undefined,
): Promise<SuccessSummary | null> {
  if (!sessionId || !hasStripe()) return null;
  try {
    const session = await getStripe().checkout.sessions.retrieve(sessionId);
    return {
      email: session.customer_details?.email ?? null,
      amountTotal: session.amount_total,
      currency: session.currency ?? "usd",
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

  return <SuccessContent summary={summary} />;
}
