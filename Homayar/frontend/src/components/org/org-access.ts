import { createContext, useContext, useLayoutEffect } from "react";

import { isOrganizationGone } from "@/lib/org-api";
import type { OrgPermission } from "@/lib/org-roles";
import type { Organization } from "@/types/organizations";

/** What the org guard learned from `GET /organizations/{id}`, for the page under it. */
export interface OrgAccess {
  organization: Organization;
  /** The server's role in this org; the only source of access. */
  role: string;
  has: (permission: OrgPermission) => boolean;
  /**
   * Call with any failed request of the page. A 404 `organization_not_found`
   * swaps the page for the no-access state and re-checks the guard; the
   * return value says whether that happened.
   */
  reportError: (error: unknown) => boolean;
}

export const OrgAccessContext = createContext<OrgAccess | null>(null);

export function useOrgAccess(): OrgAccess {
  const access = useContext(OrgAccessContext);
  if (!access) throw new Error("useOrgAccess must be used under OrgGuard");
  return access;
}

/**
 * True when one of the page's read errors says the organization is gone. The
 * guard then shows the no-access state; until it re-renders, the page should
 * show the same state rather than a generic error.
 */
export function useOrgGone(...errors: unknown[]): boolean {
  const { reportError } = useOrgAccess();
  const gone = errors.some(isOrganizationGone);
  const goneError = errors.find(isOrganizationGone);
  // A layout effect: the guard swaps in its own no-access state in the same
  // commit, so no one ever sees the page's copy of it come and go.
  useLayoutEffect(() => {
    if (goneError) reportError(goneError);
  }, [goneError, reportError]);
  return gone;
}
