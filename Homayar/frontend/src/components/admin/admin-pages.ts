import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { AdminPath } from "@/lib/navigation";

/**
 * The page behind each admin route. A path that is not listed shows the
 * shared coming-soon page. A page ticket adds its line here; the routes, the
 * guard and the sidebar all come from ADMIN_NAV and need no change.
 */
export const ADMIN_PAGES: Partial<Record<AdminPath, LazyExoticComponent<ComponentType>>> = {
  "/admin": lazy(() => import("@/components/admin/AdminOverviewPage")),
  "/admin/ai-policy": lazy(() => import("@/components/admin/AiPolicyPage")),
  "/admin/api-keys": lazy(() => import("@/components/admin/ApiKeysPage")),
  "/admin/audit": lazy(() => import("@/components/admin/AuditLogPage")),
  "/admin/locations": lazy(() => import("@/components/admin/LocationsPage")),
  "/admin/members": lazy(() => import("@/components/platform/MembersPage")),
  "/admin/privacy": lazy(() => import("@/components/admin/PrivacyPage")),
  "/admin/processing": lazy(() => import("@/components/admin/ProcessingPage")),
  "/admin/usage": lazy(() => import("@/components/admin/AdminUsagePage")),
  "/admin/settings": lazy(() => import("@/components/platform/SettingsPage")),
  "/admin/teams": lazy(() => import("@/components/admin/TeamsPage")),
};
