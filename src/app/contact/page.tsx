import type { Metadata } from "next";
import { ContactContent } from "./content";

export const metadata: Metadata = {
  title: "Contact — درخواست دمو همایار",
  description:
    "یک جلسه معرفی ۳۰ دقیقه‌ای همایار رزرو کنید. یک فایل ضبط‌شده نمونه بیاورید و با یک گزارش امتیازدهی‌شده و تحلیل‌شده توسط عامل‌های هوشمند بیرون بروید — بدون هیچ تعهدی و بدون نیاز به راه‌اندازی.",
};

export default function ContactPage() {
  return <ContactContent />;
}
