import { NextResponse } from "next/server";
import type Stripe from "stripe";
import { eq } from "drizzle-orm";
import { getStripe, hasStripe } from "@/lib/stripe";
import { getDb, hasDb } from "@/lib/db";
import { checkoutSessions, orderItems, orders } from "@/db/schema";

/**
 * Stripe webhook: records completed checkouts in Neon.
 * Endpoint to register in the Stripe dashboard (test mode):
 *   <APP_URL>/api/webhooks/stripe  — event: checkout.session.completed
 */
export async function POST(request: Request) {
  if (!hasStripe() || !process.env.STRIPE_WEBHOOK_SECRET) {
    return NextResponse.json(
      { error: "Webhook not configured." },
      { status: 503 },
    );
  }

  const signature = request.headers.get("stripe-signature");
  if (!signature) {
    return NextResponse.json({ error: "Missing signature." }, { status: 400 });
  }

  let event: Stripe.Event;
  try {
    const body = await request.text();
    event = getStripe().webhooks.constructEvent(
      body,
      signature,
      process.env.STRIPE_WEBHOOK_SECRET,
    );
  } catch (err) {
    console.error("[webhook] signature verification failed:", err);
    return NextResponse.json({ error: "Invalid signature." }, { status: 400 });
  }

  if (event.type === "checkout.session.completed") {
    const session = event.data.object;
    if (!hasDb()) {
      console.log("[webhook] checkout completed (no DB configured):", session.id);
      return NextResponse.json({ received: true });
    }
    try {
      const db = getDb();
      const [order] = await db
        .insert(orders)
        .values({
          stripeSessionId: session.id,
          email: session.customer_details?.email ?? null,
          amountTotal: session.amount_total ?? 0,
          currency: session.currency ?? "usd",
          status: "paid",
          paymentIntentId:
            typeof session.payment_intent === "string"
              ? session.payment_intent
              : (session.payment_intent?.id ?? null),
          metadata: (session.metadata ?? {}) as Record<string, string>,
        })
        .onConflictDoNothing({ target: orders.stripeSessionId })
        .returning();

      if (order) {
        // Expand each line item's product so we can read the slug we stamped on
        // it at checkout — reliable attribution regardless of line-item order.
        const lineItems = await getStripe().checkout.sessions.listLineItems(
          session.id,
          { limit: 100, expand: ["data.price.product"] },
        );
        await db.insert(orderItems).values(
          lineItems.data.map((li) => {
            const product = li.price?.product;
            const slug =
              product && typeof product === "object" && "metadata" in product
                ? (product.metadata?.slug ?? "unknown")
                : "unknown";
            return {
              orderId: order.id,
              productSlug: slug,
              name: li.description ?? "Item",
              unitAmount: li.price?.unit_amount ?? 0,
              quantity: li.quantity ?? 1,
            };
          }),
        );
        await db
          .update(checkoutSessions)
          .set({ status: "completed" })
          .where(eq(checkoutSessions.stripeSessionId, session.id));
      }
    } catch (err) {
      console.error("[webhook] failed to record order:", err);
      return NextResponse.json({ error: "Persistence failed." }, { status: 500 });
    }
  }

  return NextResponse.json({ received: true });
}
