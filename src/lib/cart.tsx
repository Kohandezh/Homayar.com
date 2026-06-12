"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { CartItem, Product } from "@/lib/types";

const STORAGE_KEY = "homayar-cart-v1";

interface CartContextValue {
  items: CartItem[];
  /** False until the cart has been hydrated from localStorage. */
  ready: boolean;
  count: number;
  subtotal: number;
  add: (product: Product, quantity?: number) => void;
  remove: (slug: string) => void;
  setQuantity: (slug: string, quantity: number) => void;
  clear: () => void;
}

const CartContext = createContext<CartContextValue | null>(null);

export function CartProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<CartItem[]>([]);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as CartItem[];
        if (Array.isArray(parsed)) setItems(parsed.filter((i) => i.quantity > 0));
      }
    } catch {
      // corrupted storage — start fresh
    }
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
    } catch {
      // storage full/unavailable — cart still works in memory
    }
  }, [items, ready]);

  const add = useCallback((product: Product, quantity = 1) => {
    if (product.billingType === "custom" || product.priceUsd === 0) return;
    setItems((prev) => {
      const existing = prev.find((i) => i.slug === product.slug);
      if (existing) {
        return prev.map((i) =>
          i.slug === product.slug
            ? { ...i, quantity: Math.min(i.quantity + quantity, 99) }
            : i,
        );
      }
      return [
        ...prev,
        {
          slug: product.slug,
          name: product.name,
          priceUsd: product.priceUsd,
          billingType: product.billingType,
          quantity: Math.min(quantity, 99),
          image: product.image,
          category: product.category,
        },
      ];
    });
  }, []);

  const remove = useCallback((slug: string) => {
    setItems((prev) => prev.filter((i) => i.slug !== slug));
  }, []);

  const setQuantity = useCallback((slug: string, quantity: number) => {
    setItems((prev) =>
      quantity <= 0
        ? prev.filter((i) => i.slug !== slug)
        : prev.map((i) =>
            i.slug === slug ? { ...i, quantity: Math.min(quantity, 99) } : i,
          ),
    );
  }, []);

  const clear = useCallback(() => setItems([]), []);

  const value = useMemo<CartContextValue>(() => {
    const count = items.reduce((sum, i) => sum + i.quantity, 0);
    const subtotal = items.reduce((sum, i) => sum + i.priceUsd * i.quantity, 0);
    return { items, ready, count, subtotal, add, remove, setQuantity, clear };
  }, [items, ready, add, remove, setQuantity, clear]);

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart(): CartContextValue {
  const ctx = useContext(CartContext);
  if (!ctx) throw new Error("useCart must be used within CartProvider");
  return ctx;
}
