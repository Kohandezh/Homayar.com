import Stripe from "stripe";

/**
 * Lazy Stripe client (test mode). When STRIPE_SECRET_KEY is missing the
 * checkout API responds with a clear "payments not configured" error instead
 * of crashing at import time, so the rest of the site keeps working.
 */
export function hasStripe(): boolean {
  return Boolean(process.env.STRIPE_SECRET_KEY);
}

let _stripe: Stripe | null = null;

export function getStripe(): Stripe {
  if (!hasStripe()) {
    throw new Error(
      "STRIPE_SECRET_KEY is not set. See .env.example for required environment variables.",
    );
  }
  if (!_stripe) {
    _stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);
  }
  return _stripe;
}
