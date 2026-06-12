"use client";

import Image from "next/image";
import Link from "next/link";
import { useState } from "react";
import { useCart } from "@/lib/cart";
import { formatPrice } from "@/lib/types";
import { ButtonLink } from "@/components/ui";
import { CartTotals } from "@/components/cart-view";

export function CheckoutView() {
  const { items, ready } = useCart();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function startCheckout() {
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch("/api/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          items: items.map((i) => ({ slug: i.slug, quantity: i.quantity })),
        }),
      });
      const data = (await res.json()) as { url?: string; error?: string };
      if (!res.ok || !data.url) {
        setError(data.error ?? "Could not start checkout. Please try again.");
        setSubmitting(false);
        return;
      }
      window.location.href = data.url;
    } catch {
      setError("Network error — please check your connection and try again.");
      setSubmitting(false);
    }
  }

  if (!ready) {
    return (
      <div className="container-site py-24">
        <div className="mx-auto h-40 max-w-xl animate-pulse rounded-2xl bg-raised" />
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="container-site flex flex-col items-center py-24 text-center">
        <h1 className="font-display text-3xl font-semibold text-ink">
          Nothing to check out yet
        </h1>
        <p className="mt-3 max-w-md text-ink-muted">
          Your cart is empty. Add plans or modules from the catalog first.
        </p>
        <ButtonLink href="/catalog" size="lg" className="mt-8">
          Browse the catalog
        </ButtonLink>
      </div>
    );
  }

  return (
    <div className="container-site py-12 sm:py-16">
      <h1 className="font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
        Checkout
      </h1>
      <p className="mt-2 text-ink-muted">
        Review your order, then pay securely on Stripe.
      </p>

      <div className="mt-10 grid gap-10 lg:grid-cols-[1fr_380px]">
        <section aria-label="Order items">
          <ul className="divide-y divide-line rounded-2xl border border-line bg-surface">
            {items.map((item) => (
              <li key={item.slug} className="flex items-center gap-4 p-4">
                <div className="relative h-14 w-14 shrink-0 overflow-hidden rounded-lg border border-line">
                  <Image
                    src={item.image}
                    alt=""
                    fill
                    sizes="56px"
                    className="object-cover"
                  />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium text-ink">{item.name}</p>
                  <p className="text-sm text-ink-muted">
                    Qty {item.quantity} ·{" "}
                    {formatPrice(item.priceUsd, item.billingType)}
                  </p>
                </div>
                <p className="font-mono text-sm text-ink">
                  ${(item.priceUsd * item.quantity).toLocaleString("en-US")}
                </p>
              </li>
            ))}
          </ul>
          <p className="mt-4 text-sm text-ink-faint">
            Need to make changes?{" "}
            <Link href="/cart" className="text-accent hover:underline">
              Edit your cart
            </Link>
            .
          </p>
        </section>

        <aside className="card-surface h-fit p-6">
          <h2 className="font-display text-lg font-semibold text-ink">
            Order summary
          </h2>
          <CartTotals />

          {error && (
            <p
              role="alert"
              className="mt-4 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2.5 text-sm text-danger"
            >
              {error}
            </p>
          )}

          <button
            type="button"
            onClick={startCheckout}
            disabled={submitting}
            className="mt-6 flex h-12 w-full items-center justify-center rounded-xl bg-accent text-base font-semibold text-bg transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting ? "Redirecting to Stripe…" : "Pay with Stripe"}
          </button>
          <p className="mt-3 text-center text-xs leading-relaxed text-ink-faint">
            Test mode: use card 4242 4242 4242 4242, any future expiry, any
            CVC. No real charges are made.
          </p>
        </aside>
      </div>
    </div>
  );
}
