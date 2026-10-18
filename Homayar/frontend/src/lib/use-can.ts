import { useMemo } from "react";
import { useAuth } from "@/lib/auth-context";
import type { AdminNavEntry } from "@/lib/navigation";
import { capabilitiesFrom, permissionsFor, type Capabilities, type Permission } from "@/lib/roles";
import type { WorkspaceRole } from "@/types/auth";

/** The role in the selected workspace; the user object never carries one. */
export function useWorkspaceRole(): WorkspaceRole | null {
  return useAuth().workspace?.role ?? null;
}

export interface Can extends Capabilities {
  role: WorkspaceRole | null;
  has: (permission: Permission) => boolean;
  /** Only when /auth/me says `system_role` is exactly "operator" (HOM-184); any other value is false. */
  isOperator: boolean;
}

/** What the selected workspace allows, recomputed when the workspace or the user changes. */
export function useCan(): Can {
  const { workspace, user } = useAuth();
  const isOperator = user?.system_role === "operator";
  return useMemo(() => {
    const permissions = permissionsFor(workspace);
    return {
      role: workspace?.role ?? null,
      has: (permission: Permission) => permissions.has(permission),
      isOperator,
      ...capabilitiesFrom(permissions),
    };
  }, [workspace, isOperator]);
}

/**
 * Whether an admin entry opens: the workspace has its permission and, for an
 * operator-only entry, the user is a platform operator. The sidebar and the
 * route guard both ask this one function, so a link never leads to a
 * no-access page and a hidden page never opens.
 */
export function canOpenAdminEntry(
  can: Pick<Can, "has" | "isOperator">,
  entry: Pick<AdminNavEntry, "permission" | "operatorOnly">,
): boolean {
  return can.has(entry.permission) && (entry.operatorOnly !== true || can.isOperator === true);
}
