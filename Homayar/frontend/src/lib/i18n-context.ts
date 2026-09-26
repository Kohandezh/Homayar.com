import { createContext, useContext } from "react";

export type Lang = "fa" | "en";

export interface I18nContextValue {
  lang: Lang;
  setLang: (language: Lang) => void;
  t: (key: string) => string;
  dir: "rtl" | "ltr";
}

export const I18nContext = createContext<I18nContextValue | null>(null);

export function useI18n() {
  const context = useContext(I18nContext);
  if (!context) throw new Error("useI18n must be used within I18nProvider");
  return context;
}
