import type { Metadata } from "next";
import { CheckoutView } from "@/components/checkout-view";

export const metadata: Metadata = {
  title: "Checkout",
  description: "Securely complete your Homayar order with Stripe.",
  robots: { index: false },
};

export default function CheckoutPage() {
  return <CheckoutView />;
}
