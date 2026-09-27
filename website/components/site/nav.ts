import type { Dictionary } from "@/i18n/en";

export type NavItem = { key: string; label: string; path: string; icon: string };

export function primaryNav(dict: Dictionary): NavItem[] {
  return [
    { key: "product", label: dict.nav.product, path: "/product", icon: "layout" },
    { key: "how-it-works", label: dict.nav.howItWorks, path: "/how-it-works", icon: "activity" },
    { key: "solutions", label: dict.nav.solutions, path: "/solutions", icon: "users" },
    { key: "security", label: dict.nav.security, path: "/security", icon: "shieldCheck" },
    { key: "deployment", label: dict.nav.deployment, path: "/deployment", icon: "server" },
    { key: "pricing", label: dict.nav.pricing, path: "/pricing", icon: "scale" },
    { key: "about", label: dict.nav.about, path: "/about", icon: "globe" },
  ];
}

export function tabBarNav(dict: Dictionary): NavItem[] {
  return [
    { key: "home", label: dict.nav.home, path: "", icon: "home" },
    { key: "product", label: dict.nav.product, path: "/product", icon: "layout" },
    { key: "solutions", label: dict.nav.solutions, path: "/solutions", icon: "users" },
    { key: "security", label: dict.nav.security, path: "/security", icon: "shieldCheck" },
    { key: "contact", label: dict.nav.contact, path: "/contact", icon: "mail" },
  ];
}
