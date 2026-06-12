"use client";

import { useEffect } from "react";
import { useCart } from "@/lib/cart";

/** Empties the cart once after a successful checkout. */
export function CartClearer() {
  const { ready, clear } = useCart();
  useEffect(() => {
    if (ready) clear();
  }, [ready, clear]);
  return null;
}
