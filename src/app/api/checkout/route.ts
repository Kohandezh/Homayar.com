import { NextResponse } from "next/server";
import { z } from "zod";
import { getProducts } from "@/lib/catalog";
import { getStripe, hasStripe } from "@/lib/stripe";
import { getDb, hasDb } from "@/lib/db";
import { checkoutSessions } from "@/db/schema";
import { SITE } from "@/lib/site";
import { enforceRateLimit } from "@/lib/rate-limit";

const checkoutSchema = z.object({
  items: z
    .array(
      z.object({
        slug: z.string().min(1),
        quantity: z.number().int().min(1).max(99),
      }),
    )
    .min(1)
    .max(50),
});

/**
 * Creates a Stripe-hosted Checkout Session from the cart.
 * Prices are always resolved server-side from the catalog — the client only
 * sends slugs and quantities, so prices cannot be tampered with.
 *
 * Mode selection: if every recurring item shares one billing interval we
 * create a real subscription. Stripe Checkout cannot mix billing intervals in a
 * single session, so carts mixing monthly + yearly items are rejected with a
 * clear message rather than silently downgraded to a one-time charge (which
 * would look like a subscription to the customer but never bill again).
 */
export async function POST(request: Request) {
  const limited = enforceRateLimit(request, "checkout", 10, 60_000);
  if (limited) return limited;

  if (!hasStripe()) {
    return NextResponse.json(
      {
        error:
          "Payments are not configured yet. Set STRIPE_SECRET_KEY (see .env.example).",
      },
      { status: 503 },
    );
  }

  let parsed: z.infer<typeof checkoutSchema>;
  try {
    parsed = checkoutSchema.parse(await request.json());
  } catch {
    return NextResponse.json({ error: "Invalid cart payload." }, { status: 400 });
  }

  const catalog = await getProducts();
  const resolved = [];
  for (const item of parsed.items) {
    const product = catalog.find((p) => p.slug === item.slug);
    if (!product) {
      return NextResponse.json(
        { error: `Unknown product: ${item.slug}` },
        { status: 400 },
      );
    }
    if (product.billingType === "custom" || product.priceUsd === 0) {
      return NextResponse.json(
        { error: `${product.name} requires a sales conversation — book a demo.` },
        { status: 400 },
      );
    }
    resolved.push({ product, quantity: item.quantity });
  }

  const intervals = new Set(
    resolved
      .filter((r) => r.product.billingType === "monthly" || r.product.billingType === "yearly")
      .map((r) => (r.product.billingType === "monthly" ? "month" : "year")),
  );

  // Stripe cannot bill monthly and yearly items in one subscription session.
  // Reject rather than silently charge once — the customer must not think they
  // subscribed when they didn't.
  if (intervals.size > 1) {
    return NextResponse.json(
      {
        error:
          "Monthly and yearly plans can't be purchased together. Please check them out in separate orders.",
      },
      { status: 400 },
    );
  }

  const useSubscription = intervals.size === 1;
  const appUrl = SITE.url;
  const httpsImages = appUrl.startsWith("https://");

  const lineItems = resolved.map(({ product, quantity }) => {
    const recurringInterval =
      product.billingType === "monthly"
        ? ("month" as const)
        : product.billingType === "yearly"
          ? ("year" as const)
          : null;
    return {
      quantity,
      price_data: {
        currency: "usd",
        unit_amount: product.priceUsd * 100,
        product_data: {
          name: product.name,
          description: product.tagline,
          // Stamp the slug on the product so the webhook attributes order items
          // by identity, not by array position (Stripe does not guarantee
          // line-item order matches creation order).
          metadata: { slug: product.slug },
          ...(httpsImages ? { images: [`${appUrl}${product.image}`] } : {}),
        },
        ...(useSubscription && recurringInterval
          ? { recurring: { interval: recurringInterval } }
          : {}),
      },
    };
  });

  try {
    const stripe = getStripe();
    const session = await stripe.checkout.sessions.create({
      mode: useSubscription ? "subscription" : "payment",
      line_items: lineItems,
      success_url: `${appUrl}/success?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${appUrl}/cart`,
      billing_address_collection: "auto",
      metadata: {
        cart: JSON.stringify(
          resolved.map((r) => ({ slug: r.product.slug, qty: r.quantity })),
        ).slice(0, 480),
      },
    });

    if (hasDb()) {
      try {
        await getDb().insert(checkoutSessions).values({
          stripeSessionId: session.id,
          status: "created",
          cart: resolved.map((r) => ({
            slug: r.product.slug,
            quantity: r.quantity,
            unitAmount: r.product.priceUsd * 100,
          })),
        });
      } catch (err) {
        console.error("[checkout] failed to record session:", err);
      }
    }

    return NextResponse.json({ url: session.url });
  } catch (err) {
    console.error("[checkout] Stripe session creation failed:", err);
    return NextResponse.json(
      { error: "Could not start checkout. Please try again." },
      { status: 502 },
    );
  }
}
