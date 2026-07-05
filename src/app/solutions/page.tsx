import type { Metadata } from "next";
import { SolutionsContent } from "./content";

export const metadata: Metadata = {
  title: "Solutions — راهکارها بر اساس صنعت و تیم | همایار",
  description:
    "همایار چگونه تحلیل صوتی هوشمند خود را با مراکز تماس بانکداری، سلامت، بیمه، مخابرات، خرده‌فروشی و دولتی هماهنگ می‌کند — و با تیم‌های کنترل کیفیت، عملیات، فروش و انطباق درون آن‌ها.",
};

export default function SolutionsPage() {
  return <SolutionsContent />;
}
