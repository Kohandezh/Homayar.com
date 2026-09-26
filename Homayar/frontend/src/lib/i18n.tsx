import { useState, useEffect, type ReactNode } from "react";
import { I18nContext, type Lang } from "@/lib/i18n-context";

const LANG_STORAGE_KEY = "homayar_lang";

interface Translations {
  [key: string]: { fa: string; en: string };
}

const translations: Translations = {
  "nav.overview": { fa: "داشبورد", en: "Overview" },
  "nav.graph": { fa: "گراف", en: "Graph" },
  "nav.copilot": { fa: "دستیار هوشمند", en: "Copilot" },
  "nav.calls": { fa: "تماس‌ها", en: "Calls" },
  "nav.incidents": { fa: "حوادث", en: "Incidents" },
  "nav.analytics": { fa: "تحلیل‌ها", en: "Analytics" },
  "nav.settings": { fa: "تنظیمات", en: "Settings" },
  "nav.logout": { fa: "خروج", en: "Logout" },
  "calls.upload": { fa: "آپلود صوت", en: "Upload Audio" },
  "calls.analyze": { fa: "تحلیل تماس", en: "Analyze Call" },
  "calls.noCalls": { fa: "تماسی یافت نشد", en: "No calls found" },
  "calls.selectCall": { fa: "یک تماس را انتخاب کنید", en: "Select a call to view details" },
  "report.title": { fa: "گزارش تماس", en: "Call Report" },
  "report.exportPdf": { fa: "خروجی PDF", en: "Export PDF" },
  "report.qc": { fa: "کنترل کیفیت", en: "Quality Control" },
  "report.problems": { fa: "تشخیص مشکل", en: "Problem Detection" },
  "report.insights": { fa: "بینش کسب‌وکار", en: "Business Insight" },
  "report.transcript": { fa: "متن مکالمه", en: "Transcript" },
  "report.actions": { fa: "اقدامات پیشنهادی", en: "Recommended Actions" },
  "common.loading": { fa: "در حال بارگذاری...", en: "Loading..." },
  "common.agent": { fa: "اپراتور", en: "Agent" },
  "common.duration": { fa: "مدت", en: "Duration" },
  "common.language": { fa: "زبان", en: "Language" },
  "common.direction": { fa: "جهت", en: "Direction" },
  "common.sentiment": { fa: "احساس", en: "Sentiment" },
  "common.severity": { fa: "شدت", en: "Severity" },
  "common.score": { fa: "امتیاز", en: "Score" },
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
