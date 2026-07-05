import type { Metadata } from "next";
import { BookingExperience } from "@/components/site/booking";

export const metadata: Metadata = {
  title: "رزرو جلسه",
  description:
    "یک زمان مناسب برای جلسه مشاوره با تیم همایار رزرو کنید. زمان‌ها به وقت محلی شما نمایش داده می‌شوند.",
  alternates: { canonical: "/book" },
};

export default function BookPage() {
  return <BookingExperience />;
}
