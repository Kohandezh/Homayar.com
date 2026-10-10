import { useState, useEffect, type ReactNode } from "react";
import { I18nProvider as AriaI18nProvider } from "@heroui/react";
import { I18nContext, type Lang } from "@/lib/i18n-context";

const LANG_STORAGE_KEY = "homayar_lang";

interface Translations {
  [key: string]: { fa: string; en: string };
}

const translations: Translations = {
};

export function I18nProvider({ children }: { children: ReactNode }) {
  // Persian first: English only when the visitor chose it earlier.
  const [lang, setLang] = useState<Lang>(() => {
    try {
      return localStorage.getItem(LANG_STORAGE_KEY) === "en" ? "en" : "fa";
    } catch {
      return "fa";
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(LANG_STORAGE_KEY, lang);
    } catch {
      /* storage unavailable: keep the in-memory choice */
    }
    document.documentElement.dir = lang === "fa" ? "rtl" : "ltr";
    document.documentElement.lang = lang;
  }, [lang]);

  function t(key: string): string {
    return translations[key]?.[lang] || key;
  }

  const dir = lang === "fa" ? "rtl" : "ltr";

  // HeroUI and React Aria read the locale from their own provider, not from
  // `document.dir`. It drives keyboard arrows, overlay placement and
  // number and date formatting.
  return (
    <I18nContext.Provider value={{ lang, setLang, t, dir }}>
      <AriaI18nProvider locale={lang === "fa" ? "fa-IR" : "en-US"}>{children}</AriaI18nProvider>
    </I18nContext.Provider>
  );
}
