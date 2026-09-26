"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useMounted } from "@/lib/client-state";
import { DIR, LOCALES, type Locale } from "@/lib/content";

/**
 * App-wide language state. One source of truth so the choice persists as the
 * visitor moves between the homepage (which renders fully-translated content)
 * and the inner pages (whose shared header/footer chrome localizes). Selection
 * is saved to localStorage and mirrored onto <html lang/dir> so RTL locales
 * (fa, ar) flip direction everywhere.
 */
const STORAGE_KEY = "homayar-locale";

type LocaleCtx = { locale: Locale; setLocale: (l: Locale) => void };

const Ctx = createContext<LocaleCtx>({ locale: "fa", setLocale: () => {} });

const DEFAULT_LOCALE: Locale = "fa";

// A returning visitor's saved choice wins; a first-time visitor gets their
// browser language when it's one we support; everyone else stays on fa.
function detectLocale(): Locale {
  if (typeof window === "undefined") return DEFAULT_LOCALE;
  try {
    const saved = localStorage.getItem(STORAGE_KEY) as Locale | null;
    if (saved && LOCALES.includes(saved)) return saved;
    const nav = (navigator.language || "").slice(0, 2).toLowerCase() as Locale;
    if (LOCALES.includes(nav)) return nav;
  } catch {
    /* storage unavailable — keep default */
  }
  return DEFAULT_LOCALE;
}

export function LocaleProvider({ children }: { children: ReactNode }) {
  // localStorage is client-only: the server render and hydration use the fa
  // default, and the detected locale applies right after mount.
  const mounted = useMounted();
  const [picked, setLocaleState] = useState<Locale>(detectLocale);
  const locale = mounted ? picked : DEFAULT_LOCALE;

  useEffect(() => {
    const html = document.documentElement;
    html.lang = locale;
    html.dir = DIR[locale];
  }, [locale]);

  function setLocale(l: Locale) {
    setLocaleState(l);
    try {
      localStorage.setItem(STORAGE_KEY, l);
    } catch {
      /* ignore */
    }
  }

  return <Ctx.Provider value={{ locale, setLocale }}>{children}</Ctx.Provider>;
}

export function useLocale() {
  return useContext(Ctx);
}
