import type { Metadata } from "next";
import { CartView } from "@/components/cart-view";

export const metadata: Metadata = {
  title: "Cart",
  description: "Review the Homayar plans, modules, and services in your cart.",
  robots: { index: false },
};

export default function CartPage() {
  return <CartView />;
}
