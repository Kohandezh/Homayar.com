import type { ReactNode } from "react";
import NoAccessPage from "@/components/admin/NoAccessPage";
import type { Permission } from "@/lib/roles";
import { useCan } from "@/lib/use-can";

/**
 * The one guard for every admin route. Without the permission the page is
 * never mounted (so none of its requests start) and the URL does not change;
 * the shared no-access page renders inside the layout instead. The API still
 * enforces every permission on its side.
 */
export default function AdminGuard({ permission, children }: { permission: Permission; children: ReactNode }) {
  const can = useCan();
  return can.has(permission) ? children : <NoAccessPage />;
}
