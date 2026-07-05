import type { Metadata } from "next";
import { PlatformContent } from "./content";

export const metadata: Metadata = {
  title: "Platform — پلتفرم همایار",
  description:
    "همایار چگونه کار می‌کند: یک خط پردازش شش‌مرحله‌ای که فایل‌های ضبط‌شده تماس را دریافت، رونویسی و با تحلیل چندعاملی هوش مصنوعی پردازش می‌کند، کیفیت را امتیاز می‌دهد، بینش‌ها را آشکار می‌سازد و مربی‌گری ارائه می‌دهد — با امنیت در سطح سازمانی.",
};

export default function PlatformPage() {
  return <PlatformContent />;
}
