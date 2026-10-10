import { useState, useEffect, type ReactNode } from "react";
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

  return (
    <I18nContext.Provider value={{ lang, setLang, t, dir }}>
      {children}
    </I18nContext.Provider>
  );
}
