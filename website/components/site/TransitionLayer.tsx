"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useSyncExternalStore, useTransition } from "react";
import { localizeDigits, type Locale } from "@/lib/i18n";
import { VoiceMark } from "./VoiceMark";

type Phase = "idle" | "cover" | "load" | "reveal";
type Gsap = typeof import("@/lib/gsap")["gsap"];

const MOTION_OK = "(prefers-reduced-motion: no-preference)";

const store = {
  phase: "idle" as Phase,
  target: null as string | null,
  coverDone: false,
  pct: 0,
  cover: null as HTMLDivElement | null,
  fill: null as HTMLDivElement | null,
  mark: null as HTMLDivElement | null,
  progress: { v: 0 },
  gsap: null as Gsap | null,
  timeout: 0,
  listeners: new Set<() => void>(),
};

function notify() {
  store.listeners.forEach((l) => l());
}
function subscribe(l: () => void) {
  store.listeners.add(l);
  return () => {
    store.listeners.delete(l);
  };
}
function setPhase(phase: Phase) {
  store.phase = phase;
  notify();
}
function attach(el: HTMLDivElement | null) {
  store.cover = el;
  store.fill = el?.querySelector<HTMLDivElement>("[data-loader-fill]") ?? null;
  store.mark = el?.querySelector<HTMLDivElement>("[data-loader-mark]") ?? null;
}

function paint() {
  const v = store.progress.v;
  if (store.fill) store.fill.style.transform = `scaleX(${v})`;
  const pct = Math.round(v * 100);
  if (pct !== store.pct) {
    store.pct = pct;
    notify();
  }
}

function internalHref(a: HTMLAnchorElement): string | null {
  if (a.target && a.target !== "_self") return null;
  if (a.hasAttribute("download") || a.dataset.noTransition !== undefined) return null;
  const url = new URL(a.href, location.href);
  if (url.origin !== location.origin) return null;
  if (url.pathname.startsWith("/api/")) return null;
  const app = process.env.NEXT_PUBLIC_APP_URL?.trim() || "/app";
  if (url.pathname === app || url.pathname.startsWith(`${app}/`)) return null;
  if (url.pathname === location.pathname && url.search === location.search) return null;
  return url.pathname + url.search + url.hash;
}

function begin(href: string, push: (href: string) => void) {
  store.target = href.split(/[?#]/)[0];
  store.coverDone = false;
  store.progress.v = 0;
  store.pct = 0;
  setPhase("cover");
  void import("@/lib/gsap").then(({ gsap }) => {
    store.gsap = gsap;
    const cover = store.cover;
    if (!cover) return;
    gsap.killTweensOf([cover, store.progress]);
    gsap.fromTo(
      cover,
      { autoAlpha: 0 },
      {
        autoAlpha: 1,
        duration: 0.24,
        ease: "power1.inOut",
        onComplete: () => {
          store.coverDone = true;
          if (store.phase === "cover") setPhase("load");
          maybeFinish();
        },
      },
    );
    gsap.fromTo(store.progress, { v: 0.12 }, { v: 0.72, duration: 4, ease: "expo.out", onUpdate: paint });
    const bars = store.mark?.querySelectorAll<SVGRectElement>(".mark-bar");
    if (bars?.length) {
      gsap.to(bars, { scaleY: 0.35, duration: 0.42, ease: "sine.inOut", yoyo: true, repeat: -1, stagger: { each: 0.08, yoyo: true, repeat: -1 } });
    }
  });
  push(href);
  window.clearTimeout(store.timeout);
  store.timeout = window.setTimeout(() => finish(), 8000);
}

function maybeFinish() {
  if (store.phase !== "load" || !store.coverDone) return;
  if (store.target && location.pathname !== store.target) return;
  finish();
}

function finish() {
  if (store.phase === "idle" || store.phase === "reveal") return;
  setPhase("reveal");
  window.clearTimeout(store.timeout);
  const done = () => {
    store.target = null;
    setPhase("idle");
  };
  const g = store.gsap;
  const cover = store.cover;
  if (!g || !cover) {
    done();
    return;
  }
  g.killTweensOf(store.progress);
  g.to(store.progress, { v: 1, duration: 0.25, ease: "power2.out", onUpdate: paint });
  requestAnimationFrame(() =>
    requestAnimationFrame(() => {
      g.to(cover, {
        autoAlpha: 0,
        duration: 0.42,
        delay: 0.12,
        ease: "power2.inOut",
        onComplete: () => {
          const bars = store.mark?.querySelectorAll<SVGRectElement>(".mark-bar");
          if (bars?.length) g.killTweensOf(bars);
          done();
        },
      });
    }),
  );
}

function onRouteCommit(pathname: string) {
  if (store.phase === "idle") return;
  if (store.target && pathname !== store.target) return;
  if (store.phase === "cover") {
    setPhase("load");
    return;
  }
  if (!store.gsap) {
    void import("@/lib/gsap").then((m) => {
      store.gsap = m.gsap;
      if (store.cover) m.gsap.set(store.cover, { autoAlpha: 1 });
      store.coverDone = true;
      maybeFinish();
    });
    return;
  }
  maybeFinish();
}

const getPhase = () => store.phase;
const getPct = () => store.pct;
const serverPhase = (): Phase => "idle";
const serverPct = () => 0;

export function TransitionLayer({ locale, loadingLabel, brand }: { locale: Locale; loadingLabel: string; brand: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const [, startTransition] = useTransition();
  const phase = useSyncExternalStore(subscribe, getPhase, serverPhase);
  const pct = useSyncExternalStore(subscribe, getPct, serverPct);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      if (!window.matchMedia(MOTION_OK).matches) return;
      const a = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a) return;
      const href = internalHref(a);
      if (!href) return;
      e.preventDefault();
      if (store.phase !== "idle") return;
      begin(href, (h) => startTransition(() => router.push(h)));
    };
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, [router, startTransition]);

  useEffect(() => {
    onRouteCommit(pathname);
  }, [pathname]);

  const active = phase !== "idle";
  return (
    <div ref={attach} className={`cover ${active ? "is-active" : ""}`} data-testid="page-cover" data-phase={phase}>
      <div className="loader" role="status" aria-live="polite" aria-busy={active && phase !== "reveal"}>
        <div className="inline-grid size-16 place-items-center rounded-2xl bg-accent text-on-accent shadow-[0_0_48px_-6px_rgb(var(--t-glow)/0.7)]" data-loader-mark>
          <VoiceMark size={34} />
        </div>
        <div className="loader-bar" aria-hidden="true">
          <div className="loader-fill" data-loader-fill data-testid="loader-fill" />
        </div>
        <p className="label text-xs text-ink-2">
          <span className="sr-only">{brand} </span>
          {active ? loadingLabel : ""}
          <span aria-hidden="true"> {active ? localizeDigits(pct, locale) + (locale === "fa" ? "٪" : "%") : ""}</span>
        </p>
      </div>
    </div>
  );
}
