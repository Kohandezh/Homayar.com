import type { Metadata } from "next";
import { FaqContent } from "./content";

export const metadata: Metadata = {
  title: "FAQ — پرسش‌های متداول",
  description:
    "پاسخ‌های روشن درباره همایار: اینکه عامل‌های هوشمند چگونه تماس‌ها را تحلیل می‌کنند، پشتیبانی از زبان‌ها، دقت، نگهداری داده، حذف داده‌های شخصی، قیمت‌گذاری، گزینه‌های استقرار و یکپارچه‌سازی‌ها.",
};

export default function FaqPage() {
  return <FaqContent />;
}
