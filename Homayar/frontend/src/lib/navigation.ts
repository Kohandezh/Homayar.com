import {
  Activity,
  AudioLines,
  BrainCircuit,
  Gauge,
  KeyRound,
  LayoutDashboard,
  LockKeyhole,
  MapPin,
  ScrollText,
  Search,
  Settings,
  ShieldCheck,
  UserCog,
  Users,
  type LucideIcon,
} from "lucide-react";
import type { Permission } from "@/lib/roles";

export interface NavLabel {
  fa: string;
  en: string;
}

export interface MainNavEntry {
  path: string;
  label: NavLabel;
  icon: LucideIcon;
}

export interface AdminNavEntry extends MainNavEntry {
  /** The permission the backend needs to load the page; the route guard checks it. */
  permission: Permission;
}

/** Workspace pages every member sees. */
export const MAIN_NAV: readonly MainNavEntry[] = [
  { path: "/", label: { fa: "نمای کلی", en: "Overview" }, icon: LayoutDashboard },
  { path: "/meetings", label: { fa: "جلسه‌ها", en: "Meetings" }, icon: AudioLines },
  { path: "/search", label: { fa: "جست‌وجو", en: "Search" }, icon: Search },
];

/**
 * The admin area: one entry per route. The sidebar shows an entry and the
 * route guard opens its page only when the selected workspace has
 * `permission`.
 */
export const ADMIN_NAV = [
  { path: "/admin", label: { fa: "نمای کلی مدیریت", en: "Admin overview" }, icon: ShieldCheck, permission: "workspace:manage" },
  { path: "/admin/members", label: { fa: "اعضا", en: "Members" }, icon: Users, permission: "member:read" },
  { path: "/admin/teams", label: { fa: "تیم‌ها", en: "Teams" }, icon: UserCog, permission: "team:read" },
  { path: "/admin/settings", label: { fa: "تنظیمات", en: "Settings" }, icon: Settings, permission: "workspace:manage" },
  { path: "/admin/audit", label: { fa: "گزارش رویدادها", en: "Audit log" }, icon: ScrollText, permission: "audit:read" },
  { path: "/admin/usage", label: { fa: "مصرف", en: "Usage" }, icon: Gauge, permission: "billing:read" },
  { path: "/admin/processing", label: { fa: "پردازش", en: "Processing" }, icon: Activity, permission: "workspace:manage" },
  { path: "/admin/api-keys", label: { fa: "کلیدهای API", en: "API keys" }, icon: KeyRound, permission: "api_key:manage" },
  { path: "/admin/ai-policy", label: { fa: "سیاست هوش مصنوعی", en: "AI policy" }, icon: BrainCircuit, permission: "workspace:manage" },
  { path: "/admin/privacy", label: { fa: "حریم خصوصی", en: "Privacy" }, icon: LockKeyhole, permission: "pii:original_access" },
  { path: "/admin/locations", label: { fa: "مکان‌های خدمات", en: "Locations" }, icon: MapPin, permission: "workspace:manage" },
] as const satisfies readonly AdminNavEntry[];

export type AdminPath = (typeof ADMIN_NAV)[number]["path"];
