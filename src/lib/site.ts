export const SITE = {
  name: "Homayar",
  legalName: "Kohan System Farda",
  domain: "homayar.com",
  // Single source of truth for the canonical origin. Defaults to the production
  // domain so sitemap/robots/OG never fall back to localhost in a deploy where
  // NEXT_PUBLIC_APP_URL was forgotten. Override per-environment via the env var.
  url: process.env.NEXT_PUBLIC_APP_URL ?? "https://homayar.com",
  tagline: "Every call has a companion.",
  description:
    "Homayar is an agentic AI voice analyzer for call centers and enterprise customer-service teams. AI agents listen, analyze, score, and coach — so every customer conversation becomes insight.",
  contactEmail: "info@ksf.ir",
  twitter: "@homayar",
} as const;

export const NAV_LINKS = [
  { href: "/platform", label: "Platform" },
  { href: "/ai-agents", label: "AI Agents" },
  { href: "/solutions", label: "Solutions" },
  { href: "/pricing", label: "Pricing" },
  { href: "/catalog", label: "Catalog" },
  { href: "/case-studies", label: "Case Studies" },
  { href: "/blog", label: "Blog" },
] as const;

export const FOOTER_GROUPS = [
  {
    title: "Product",
    links: [
      { href: "/platform", label: "Platform" },
      { href: "/ai-agents", label: "AI Agents" },
      { href: "/catalog", label: "Catalog" },
      { href: "/pricing", label: "Pricing" },
      { href: "/solutions", label: "Solutions" },
    ],
  },
  {
    title: "Company",
    links: [
      { href: "/about", label: "About" },
      { href: "/case-studies", label: "Case Studies" },
      { href: "/blog", label: "Blog & Insights" },
      { href: "/contact", label: "Contact / Book a Demo" },
    ],
  },
  {
    title: "Resources",
    links: [
      { href: "/faq", label: "FAQ" },
      { href: "/privacy", label: "Privacy Policy" },
      { href: "/terms", label: "Terms of Service" },
    ],
  },
] as const;

export function absoluteUrl(path: string): string {
  return `${SITE.url}${path.startsWith("/") ? path : `/${path}`}`;
}
