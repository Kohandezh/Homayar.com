import { useMemo, useState } from "react";
import { Button, Card, Table } from "@heroui/react";
import { Plus, UserPlus } from "lucide-react";

import CreateOrgWorkspaceModal from "@/components/org/CreateOrgWorkspaceModal";
import InstallWorkspaceMemberModal from "@/components/org/InstallWorkspaceMemberModal";
import { useOrgAccess, useOrgGone } from "@/components/org/org-access";
import { ORG_COPY } from "@/components/org/org-copy";
import OrgNoAccess from "@/components/org/OrgNoAccess";
import { DayText, OrgPageHeader, OrgReadError } from "@/components/org/OrgUi";
import { EmptyState, LoadingState, StatusBadge } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { useOrgWorkspaces } from "@/lib/org-api";
import { roleLabel } from "@/lib/roles";
import type { WorkspaceRole } from "@/types/auth";
import type { OrgWorkspace } from "@/types/organizations";

/**
 * The organization's workspaces (HOM-193). Analysts and executives see only
 * the workspaces granted to them; the server filters the list.
 */
export default function OrgWorkspacesPage() {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const { organization, has } = useOrgAccess();
  const canCreate = has("organization:workspace:create");
  const canAdminister = has("organization:workspace:administer");
  const workspacesQuery = useOrgWorkspaces(organization.id);
  const gone = useOrgGone(workspacesQuery.error);
  const workspaces = useMemo(() => workspacesQuery.data?.items ?? [], [workspacesQuery.data]);
  const [creating, setCreating] = useState(false);
  const [installing, setInstalling] = useState<OrgWorkspace | null>(null);

  if (gone) return <OrgNoAccess />;

  return (
    <div className="platform-page space-y-5">
      <OrgPageHeader
        eyebrow={organization.name}
        name="eyebrow"
        title={copy.workspacesTitle}
        actions={
          canCreate ? (
            <Button size="sm" variant="primary" onPress={() => setCreating(true)}>
              <Plus aria-hidden="true" size={15} />
              {copy.createWorkspace}
            </Button>
          ) : undefined
        }
      />

      <Card<"section"> render={(props) => <section {...props} />} className="min-w-0">
        <Card.Content className="min-w-0" aria-busy={workspacesQuery.isPending || undefined}>
          {workspacesQuery.isPending ? (
            <LoadingState label={copy.loadingWorkspaces} />
          ) : workspacesQuery.isError ? (
            <OrgReadError
              error={workspacesQuery.error}
              fallback={copy.workspacesFailed}
              onRetry={() => void workspacesQuery.refetch()}
            />
          ) : workspaces.length === 0 ? (
            <EmptyState label={copy.noWorkspaces} />
          ) : (
            <Table className="min-w-0">
              <Table.ScrollContainer
                role="region"
                tabIndex={0}
                aria-label={copy.tableScrolls(copy.workspacesLabel)}
                className="platform-members-table-scroll"
              >
                <Table.Content aria-label={copy.workspacesLabel}>
                  <Table.Header>
                    <Table.Column id="workspace" isRowHeader>
                      {copy.columnWorkspace}
                    </Table.Column>
                    <Table.Column id="status">{copy.columnStatus}</Table.Column>
                    <Table.Column id="role">{copy.columnYourRole}</Table.Column>
                    <Table.Column id="created">{copy.columnCreated}</Table.Column>
                    <Table.Column id="actions">{copy.columnActions}</Table.Column>
                  </Table.Header>
                  <Table.Body items={workspaces} dependencies={[lang, canAdminister]}>
                    {(workspace: OrgWorkspace) => (
                      <Table.Row id={workspace.id}>
                        <Table.Cell>
                          <span className="flex flex-col">
                            <span className="font-semibold text-foreground" dir="auto">
                              {workspace.name}
                            </span>
                            <span className="text-xs text-muted" dir="ltr">
                              {workspace.slug}
                            </span>
                          </span>
                        </Table.Cell>
                        <Table.Cell>
                          <StatusBadge status={workspace.status} />
                        </Table.Cell>
                        <Table.Cell>
                          {workspace.my_workspace_role
                            ? roleLabel(workspace.my_workspace_role as WorkspaceRole, lang)
                            : copy.none}
                        </Table.Cell>
                        <Table.Cell>
                          <DayText value={workspace.created_at} />
                        </Table.Cell>
                        <Table.Cell>
                          {canAdminister && (
                            <Button
                              size="sm"
                              variant="ghost"
                              aria-label={copy.addMemberTo(workspace.name)}
                              onPress={() => setInstalling(workspace)}
                            >
                              <UserPlus aria-hidden="true" size={14} />
                              {copy.addMember}
                            </Button>
                          )}
                        </Table.Cell>
                      </Table.Row>
                    )}
                  </Table.Body>
                </Table.Content>
              </Table.ScrollContainer>
            </Table>
          )}
        </Card.Content>
      </Card>

      {canCreate && creating && <CreateOrgWorkspaceModal onClose={() => setCreating(false)} />}
      {canAdminister && installing && (
        <InstallWorkspaceMemberModal workspace={installing} onClose={() => setInstalling(null)} />
      )}
    </div>
  );
}
