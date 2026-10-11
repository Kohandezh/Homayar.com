import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { AdminPath } from "@/lib/navigation";

/**
 * The page behind each admin route. A path that is not listed shows the
 * shared coming-soon page. A page ticket adds its line here; the routes, the
 * guard and the sidebar all come from ADMIN_NAV and need no change.
 */
export const ADMIN_PAGES: Partial<Record<AdminPath, LazyExoticComponent<ComponentType>>> = {
  "/admin": lazy(() => import("@/components/admin/AdminOverviewPage")),
  "/admin/members": lazy(() => import("@/components/platform/MembersPage")),
  "/admin/usage": lazy(() => import("@/components/platform/UsagePage")),
  "/admin/settings": lazy(() => import("@/components/platform/SettingsPage")),
};
