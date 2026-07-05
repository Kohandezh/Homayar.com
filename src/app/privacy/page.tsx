import type { Metadata } from "next";
import { PrivacyContent } from "./content";

export const metadata: Metadata = {
  title: "سیاست حفظ حریم خصوصی — همایار",
  description:
    "همایار چگونه داده‌های شخصی را جمع‌آوری، استفاده و محافظت می‌کند — شامل فایل‌های ضبط‌شده تماس، رونوشت‌ها و تحلیل‌های مشتق‌شده از هوش مصنوعی که از طرف مشتریان سازمانی پردازش می‌شوند.",
};

export default function PrivacyPage() {
  return <PrivacyContent />;
}
