"use client";

import { useEffect, useImperativeHandle, useRef, type Ref } from "react";
import type { Locale } from "@/lib/i18n";

// Cloudflare's documented test site key that always passes. Used when no real key
// is set at build time (local dev and CI).
// https://developers.cloudflare.com/turnstile/troubleshooting/testing/
const testSiteKey = "1x00000000000000000000AA";
const siteKey = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY || testSiteKey;

// Must be loaded from this exact URL. Cloudflare says a proxied or cached copy breaks.
const scriptSrc = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

interface TurnstileApi {
  render(container: HTMLElement, options: Record<string, unknown>): string | undefined;
  reset(widgetId: string): void;
  remove(widgetId: string): void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

let loading: Promise<TurnstileApi> | null = null;

// Adds the script once per page. Only the contact form renders this component,
// so other pages never load it.
function loadTurnstile(): Promise<TurnstileApi> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  loading ??= new Promise<TurnstileApi>((resolve, reject) => {
    const script = document.createElement("script");
    // On failure the next mount (for example after a reload of the form) tries again.
    const fail = (reason: string) => {
      loading = null;
      script.remove();
      reject(new Error(reason));
    };
    script.src = scriptSrc;
    script.async = true;
    script.onload = () => (window.turnstile ? resolve(window.turnstile) : fail("turnstile missing after load"));
    script.onerror = () => fail("turnstile failed to load");
    document.head.appendChild(script);
  });
  return loading;
}

export interface TurnstileHandle {
  /** Gets a new token. A token works once, so call this after every submit. */
  reset(): void;
}

export function Turnstile({
  locale,
  onToken,
  onLoadError,
  ref,
}: {
  locale: Locale;
  /** A fresh token, or null when the token expired or the check failed. */
  onToken: (token: string | null) => void;
  /** The script could not load (blocked or offline), so no token will ever come. */
  onLoadError: () => void;
  ref?: Ref<TurnstileHandle>;
}) {
  const container = useRef<HTMLDivElement>(null);
  const widgetId = useRef<string | null>(null);
  const onTokenRef = useRef(onToken);
  const onLoadErrorRef = useRef(onLoadError);

  useEffect(() => {
    onTokenRef.current = onToken;
    onLoadErrorRef.current = onLoadError;
  }, [onToken, onLoadError]);

  useImperativeHandle(ref, () => ({
    reset() {
      onTokenRef.current(null);
      if (widgetId.current && window.turnstile) window.turnstile.reset(widgetId.current);
    },
  }));

  useEffect(() => {
    let cancelled = false;
    loadTurnstile()
      .then((api) => {
        if (cancelled || !container.current) return;
        widgetId.current =
          api.render(container.current, {
            sitekey: siteKey,
            language: locale,
            theme: "auto",
            callback: (token: string) => onTokenRef.current(token),
            "expired-callback": () => onTokenRef.current(null),
            "error-callback": () => onTokenRef.current(null),
          }) ?? null;
      })
      .catch(() => {
        if (cancelled) return;
        onTokenRef.current(null);
        onLoadErrorRef.current();
      });
    return () => {
      cancelled = true;
      if (widgetId.current && window.turnstile) window.turnstile.remove(widgetId.current);
      widgetId.current = null;
    };
  }, [locale]);

  return <div ref={container} className="min-h-[65px]" data-testid="turnstile" />;
}
