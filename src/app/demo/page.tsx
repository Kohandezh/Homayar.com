import type { Metadata } from "next";
import { DemoExperience } from "@/components/site/demo";

export const metadata: Metadata = {
  title: "دموی تعاملی",
  description:
    "دموی تعاملی همایار: یک تماس را انتخاب کنید و ببینید همایار چطور هم‌زمان احساس، موضوع، قصد و کیفیت مکالمه را تشخیص می‌دهد. درخواست دموی اختصاصی روی مکالمات خودتان.",
  keywords: [
    "دمو همایار",
    "دموی هوش مکالمه",
    "تحلیل زندهٔ تماس",
    "نمایش تحلیل مکالمه",
    "Homayar demo",
    "conversation intelligence demo",
    "call analysis demo",
  ],
  alternates: { canonical: "/demo" },
  openGraph: {
    type: "website",
    url: "/demo",
    title: "دموی تعاملی همایار — تحلیل زندهٔ یک مکالمه",
    description:
      "یک تماس نمونه را پخش کنید و ببینید همایار هم‌زمان احساس، موضوع، قصد و کیفیت را تشخیص می‌دهد. سپس دموی اختصاصی روی مکالمات خودتان بگیرید.",
    images: [{ url: "/og.png", width: 1200, height: 630, alt: "دموی تعاملی همایار" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "دموی تعاملی همایار — تحلیل زندهٔ یک مکالمه",
    description:
      "یک تماس نمونه را پخش کنید و تحلیل زندهٔ همایار را ببینید. دموی اختصاصی روی داده‌های خودتان.",
    images: ["/og.png"],
  },
};

export default function DemoPage() {
  return <DemoExperience />;
}
