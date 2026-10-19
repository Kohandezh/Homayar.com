import { useCallback, useMemo, useState, type ReactNode } from "react";
import { useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";

import { OrgAccessContext, type OrgAccess } from "@/components/org/org-access";
import { ORG_COPY } from "@/components/org/org-copy";
import OrgNoAccess from "@/components/org/OrgNoAccess";
import { OrgReadError } from "@/components/org/OrgUi";
import { LoadingState } from "@/components/platform/Ui";
import { apiErrorInfo } from "@/lib/api-errors";
import { useI18n } from "@/lib/i18n-context";
import { isOrganizationGone, orgQueryKeys, useOrganization } from "@/lib/org-api";
import { orgPermissionsFor, type OrgPermission } from "@/lib/org-roles";

/** A page's 404 `organization_not_found`: when it came, and whether it is final. */
interface OrgLoss {
  id: string;
  at: number;
  final: boolean;
}

/**
 * The one guard for every `/org/:organizationId` route. It reads the
 * organization, and only that answer's `role` decides access: never the
 * switcher list, never the workspace role. Without the route's permission,
 * or on any 404, the page is never mounted (so none of its requests start)
 * and the URL does not change. The API still enforces every permission.
 */
export default function OrgGuard({ permission, children }: { permission: OrgPermission; children: ReactNode }) {
  const { organizationId = "" } = useParams();
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const queryClient = useQueryClient();
  const query = useOrganization(organizationId);
  // Set when a page request said the organization is gone. It ends once a
  // newer guard read succeeds (the re-check below), so the page gets one more
  // try. A second loss right after that try is final, so a page that keeps
  // failing cannot make the guard re-check forever.
  const [loss, setLoss] = useState<OrgLoss | null>(null);

  const reportError = useCallback(
    (error: unknown) => {
      if (!isOrganizationGone(error)) return false;
      const recheckedAt = queryClient.getQueryState(orgQueryKeys.detail(organizationId))?.dataUpdatedAt ?? 0;
      setLoss((previous) => ({
        id: organizationId,
        at: Date.now(),
        final: previous !== null && previous.id === organizationId && (previous.final || recheckedAt > previous.at),
      }));
      void queryClient.invalidateQueries({ queryKey: orgQueryKeys.all });
      return true;
    },
    [organizationId, queryClient],
  );

  const organization = query.data;
  const access = useMemo<OrgAccess | null>(() => {
    if (!organization) return null;
    const permissions = orgPermissionsFor(organization.role);
    return {
      organization,
      role: organization.role,
      has: (candidate) => permissions.has(candidate),
      reportError,
    };
  }, [organization, reportError]);

  const rechecked = query.isSuccess && loss !== null && query.dataUpdatedAt > loss.at;
  if (loss !== null && loss.id === organizationId && (loss.final || !rechecked)) return <OrgNoAccess />;
  // A 404 of any code: not a member, an ended membership, or no such org.
  if (query.isError && apiErrorInfo(query.error).status === 404) return <OrgNoAccess />;
  if (access) {
    if (!access.has(permission)) return <OrgNoAccess />;
    return <OrgAccessContext.Provider value={access}>{children}</OrgAccessContext.Provider>;
  }
  if (query.isError) {
    return (
      <div className="platform-page">
        <OrgReadError error={query.error} fallback={copy.organizationLoadFailed} onRetry={() => void query.refetch()} />
      </div>
    );
  }
  return <LoadingState label={copy.loadingOrganization} />;
}
