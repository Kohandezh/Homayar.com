import { useMemo } from "react";
import { useAuth } from "@/lib/auth-context";
import { capabilitiesFrom, permissionsFor, type Capabilities, type Permission } from "@/lib/roles";
import type { WorkspaceRole } from "@/types/auth";

/** The role in the selected workspace; the user object never carries one. */
export function useWorkspaceRole(): WorkspaceRole | null {
  return useAuth().workspace?.role ?? null;
}

export interface Can extends Capabilities {
  role: WorkspaceRole | null;
  has: (permission: Permission) => boolean;
}

/** What the selected workspace allows, recomputed when the workspace changes. */
export function useCan(): Can {
  const { workspace } = useAuth();
  return useMemo(() => {
    const permissions = permissionsFor(workspace);
    return {
      role: workspace?.role ?? null,
      has: (permission: Permission) => permissions.has(permission),
      ...capabilitiesFrom(permissions),
    };
  }, [workspace]);
}
