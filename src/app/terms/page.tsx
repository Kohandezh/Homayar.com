import type { Metadata } from "next";
import { TermsContent } from "./content";

export const metadata: Metadata = {
  title: "شرایط استفاده از خدمات — همایار",
  description:
    "شرایط حاکم بر دسترسی به پلتفرم همایار و استفاده از آن — اشتراک‌ها، استفاده مجاز، مالکیت داده‌های مشتری و مسئولیت‌های کسب رضایت برای ضبط تماس.",
};

export default function TermsPage() {
  return <TermsContent />;
}
