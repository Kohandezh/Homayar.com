export type BillingType =
  | "monthly"
  | "yearly"
  | "one-time"
  | "implementation"
  | "custom";

export type TeamSize = "startup" | "smb" | "mid-market" | "enterprise";

export type Industry =
  | "banking"
  | "healthcare"
  | "insurance"
  | "telecom"
  | "retail"
  | "government"
  | "technology";

export interface Product {
  slug: string;
  name: string;
  category: string;
  tagline: string;
  shortDescription: string;
  longDescription: string;
  whoItsFor: string;
  keyOutcomes: string[];
  priceUsd: number;
  billingType: BillingType;
  features: string[];
  seoTitle: string;
  seoDescription: string;
  seoKeywords: string[];
  teamSize: TeamSize[];
  industries: Industry[];
  popular: boolean;
  recommended: boolean;
  imageConcept: string;
  /** Public path to the product artwork, e.g. /products/<slug>.png */
  image: string;
}

export interface Category {
  slug: string;
  name: string;
  description: string;
  icon: string;
}

export interface CaseStudyResult {
  metric: string;
  value: string;
}

export interface CaseStudy {
  slug: string;
  company: string;
  industry: string;
  logoInitials: string;
  headline: string;
  summary: string;
  challenge: string;
  solution: string;
  results: CaseStudyResult[];
  quote: string;
  quoteAuthor: string;
  teamSize: string;
  seoTitle: string;
  seoDescription: string;
}

export interface BlogPost {
  slug: string;
  title: string;
  excerpt: string;
  contentMarkdown: string;
  author: string;
  dateISO: string;
  tags: string[];
  readingMinutes: number;
  seoTitle: string;
  seoDescription: string;
}

export interface FaqItem {
  question: string;
  answer: string;
  group: string;
}

export interface CartItem {
  slug: string;
  name: string;
  priceUsd: number;
  billingType: BillingType;
  quantity: number;
  image: string;
  category: string;
}

export const BILLING_LABELS: Record<BillingType, string> = {
  monthly: "/month",
  yearly: "/year",
  "one-time": "one-time",
  implementation: "implementation",
  custom: "custom pricing",
};

export function formatPrice(priceUsd: number, billingType: BillingType): string {
  if (billingType === "custom" || priceUsd === 0) return "Custom";
  const amount = priceUsd.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
  if (billingType === "monthly") return `${amount}/mo`;
  if (billingType === "yearly") return `${amount}/yr`;
  return amount;
}
