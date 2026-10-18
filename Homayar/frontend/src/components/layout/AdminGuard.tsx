import type { ReactNode } from "react";
import NoAccessPage from "@/components/admin/NoAccessPage";
import type { AdminNavEntry } from "@/lib/navigation";
import { canOpenAdminEntry, useCan } from "@/lib/use-can";

/**
 * The one guard for every admin route. When the entry does not open (no
 * permission, or an operator-only entry for someone who is not an operator)
 * the page is never mounted, so none of its requests start, and the URL does
 * not change; the shared no-access page renders inside the layout instead.
 * The API still enforces every permission on its side.
 */
export default function AdminGuard({
  entry,
  children,
}: {
  entry: Pick<AdminNavEntry, "permission" | "operatorOnly">;
  children: ReactNode;
}) {
  const can = useCan();
  return canOpenAdminEntry(can, entry) ? children : <NoAccessPage />;
}
