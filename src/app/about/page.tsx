import type { Metadata } from "next";
import { AboutContent } from "./content";

export const metadata: Metadata = {
  title: "About — هر تماس، یک همراه دارد",
  description:
    "چرا همایار وجود دارد، داستان فارسی پشتِ این نام، ارزش‌هایی که محصول را شکل می‌دهند، و تیم کهن سیستم فردا که در حال ساختِ تحلیل صوتی هوش مصنوعی عامل‌محور برای مراکز تماس است.",
};

export default function AboutPage() {
  return <AboutContent />;
}
