import type { Metadata } from "next";
import { AiAgentsContent } from "./content";

export const metadata: Metadata = {
  title: "AI Agents — عامل‌های هوشمند همایار",
  description:
    "با هفت عامل هوشمند تخصصی همایار آشنا شوید — شنونده، رونویس، داور، نگهبان، کاوشگر، مربی و تحلیل‌گر — که روی هر تماس با هم همکاری می‌کنند و هر یافته را به شواهد رونوشت مستند می‌سازند.",
};

export default function AiAgentsPage() {
  return <AiAgentsContent />;
}
