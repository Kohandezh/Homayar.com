"use client";

import { useSyncExternalStore } from "react";

/**
 * Hydration-safe reads of browser-only state. The server render and the
 * hydrating client render see the server value; the client value follows
 * right after, without a setState-in-effect cascade.
 */

const noSubscription = () => () => {};

/** False during SSR and the first client render, true afterwards. */
export function useMounted() {
  return useSyncExternalStore(
    noSubscription,
    () => true,
    () => false,
  );
}

/** Media-query match: false on the server, live on the client. */
export function useMedia(query: string) {
  return useSyncExternalStore(
    (onChange) => {
      const mq = window.matchMedia(query);
      mq.addEventListener("change", onChange);
      return () => mq.removeEventListener("change", onChange);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}
