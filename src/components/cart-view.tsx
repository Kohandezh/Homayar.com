"use client";

import Image from "next/image";
import Link from "next/link";
import { useCart } from "@/lib/cart";
import { BILLING_LABELS, formatPrice } from "@/lib/types";
import { ButtonLink } from "@/components/ui";
import { CartIcon, MinusIcon, PlusIcon, TrashIcon } from "@/components/icons";

export function CartView() {
  const { items, ready, subtotal, count, remove, setQuantity } = useCart();

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
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-line bg-raised text-ink-muted">
          <CartIcon width={28} height={28} />
        </div>
        <h1 className="mt-6 font-display text-3xl font-semibold text-ink">
          Your cart is empty
        </h1>
        <p className="mt-3 max-w-md text-ink-muted">
          Browse the catalog to add platform plans, AI analysis modules, and
          services — or book a demo and we&apos;ll tailor a package for you.
        </p>
        <div className="mt-8 flex gap-3">
          <ButtonLink href="/catalog" size="lg">
            Browse the catalog
          </ButtonLink>
          <ButtonLink href="/contact" variant="secondary" size="lg">
            Book a demo
          </ButtonLink>
        </div>
      </div>
    );
  }

  return (
    <div className="container-site py-12 sm:py-16">
      <h1 className="font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
        Cart
      </h1>
      <p className="mt-2 text-ink-muted">
        {count} item{count === 1 ? "" : "s"} ready for checkout.
      </p>

      <div className="mt-10 grid gap-10 lg:grid-cols-[1fr_360px]">
        <ul className="space-y-4">
          {items.map((item) => (
            <li
              key={item.slug}
              className="card-surface flex gap-4 p-4 sm:gap-6 sm:p-5"
            >
              <Link
                href={`/catalog/${item.slug}`}
                className="relative block h-20 w-20 shrink-0 overflow-hidden rounded-xl border border-line sm:h-24 sm:w-24"
              >
                <Image
                  src={item.image}
                  alt=""
                  fill
                  sizes="96px"
                  className="object-cover"
                />
              </Link>
              <div className="flex min-w-0 flex-1 flex-col">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <Link
                      href={`/catalog/${item.slug}`}
                      className="font-medium text-ink hover:text-accent"
                    >
                      {item.name}
                    </Link>
                    <p className="mt-0.5 text-sm text-ink-muted">
                      {formatPrice(item.priceUsd, item.billingType)}{" "}
                      {item.billingType === "one-time" ||
                      item.billingType === "implementation"
                        ? `· ${BILLING_LABELS[item.billingType]}`
                        : ""}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => remove(item.slug)}
                    aria-label={`Remove ${item.name} from cart`}
                    className="rounded-lg p-2 text-ink-faint transition-colors hover:bg-raised hover:text-danger"
                  >
                    <TrashIcon width={18} height={18} />
                  </button>
                </div>
                <div className="mt-auto flex items-center justify-between pt-3">
                  <div className="flex items-center rounded-lg border border-line">
                    <button
                      type="button"
                      onClick={() => setQuantity(item.slug, item.quantity - 1)}
                      aria-label={`Decrease quantity of ${item.name}`}
                      className="flex h-9 w-9 items-center justify-center text-ink-muted hover:text-ink"
                    >
                      <MinusIcon width={16} height={16} />
                    </button>
                    <span
                      aria-live="polite"
                      className="w-10 text-center text-sm font-medium text-ink"
                    >
                      {item.quantity}
                    </span>
                    <button
                      type="button"
                      onClick={() => setQuantity(item.slug, item.quantity + 1)}
                      aria-label={`Increase quantity of ${item.name}`}
                      className="flex h-9 w-9 items-center justify-center text-ink-muted hover:text-ink"
                    >
                      <PlusIcon width={16} height={16} />
                    </button>
                  </div>
                  <p className="font-mono text-sm font-medium text-ink">
                    $
                    {(item.priceUsd * item.quantity).toLocaleString("en-US")}
                    {item.billingType === "monthly"
                      ? "/mo"
                      : item.billingType === "yearly"
                        ? "/yr"
                        : ""}
                  </p>
                </div>
              </div>
            </li>
          ))}
        </ul>

        <aside className="card-surface h-fit p-6">
          <h2 className="font-display text-lg font-semibold text-ink">
            Summary
          </h2>
          <CartTotals />
          <ButtonLink href="/checkout" size="lg" className="mt-6 w-full">
            Proceed to checkout
          </ButtonLink>
          <p className="mt-3 text-center text-xs text-ink-faint">
            Stripe test mode — no real charges.
          </p>
          <Link
            href="/catalog"
            className="mt-4 block text-center text-sm text-ink-muted hover:text-ink"
          >
            Continue browsing →
          </Link>
        </aside>
      </div>
    </div>
  );
}

export function CartTotals() {
  const { items, subtotal } = useCart();
  const monthly = items
    .filter((i) => i.billingType === "monthly")
    .reduce((s, i) => s + i.priceUsd * i.quantity, 0);
  const yearly = items
    .filter((i) => i.billingType === "yearly")
    .reduce((s, i) => s + i.priceUsd * i.quantity, 0);
  const oneTime = items
    .filter(
      (i) => i.billingType === "one-time" || i.billingType === "implementation",
    )
    .reduce((s, i) => s + i.priceUsd * i.quantity, 0);

  const rows = [
    { label: "Monthly subscriptions", value: monthly, suffix: "/mo" },
    { label: "Yearly subscriptions", value: yearly, suffix: "/yr" },
    { label: "One-time & services", value: oneTime, suffix: "" },
  ].filter((r) => r.value > 0);

  return (
    <dl className="mt-4 space-y-2.5 border-t border-line pt-4 text-sm">
      {rows.map((r) => (
        <div key={r.label} className="flex justify-between">
          <dt className="text-ink-muted">{r.label}</dt>
          <dd className="font-mono text-ink">
            ${r.value.toLocaleString("en-US")}
            {r.suffix}
          </dd>
        </div>
      ))}
      <div className="flex justify-between border-t border-line pt-3 text-base">
        <dt className="font-medium text-ink">Due at checkout</dt>
        <dd className="font-mono font-semibold text-accent">
          ${subtotal.toLocaleString("en-US")}
        </dd>
      </div>
    </dl>
  );
}
