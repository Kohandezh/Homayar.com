import { useMemo, useState } from "react";
import { Button, Card, Chip, Table } from "@heroui/react";
import { Plus, ShieldMinus } from "lucide-react";

import AddScopeGrantModal from "@/components/org/AddScopeGrantModal";
import { useOrgAccess, useOrgGone } from "@/components/org/org-access";
import { ORG_COPY } from "@/components/org/org-copy";
import OrgNoAccess from "@/components/org/OrgNoAccess";
import { DayText, OrgPageHeader, OrgReadError } from "@/components/org/OrgUi";
import RevokeScopeGrantDialog from "@/components/org/RevokeScopeGrantDialog";
import { EmptyState, LoadingState } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { useOrgMembers, useOrgWorkspaces, useScopeGrants } from "@/lib/org-api";
import { grantMemberName, scopeGrantRows, type ScopeGrantRow } from "@/components/org/org-model";

/**
 * Scope grants (HOM-193): which workspace each analyst or executive may see.
 * Names come from the member and workspace lists; a grant whose member or
 * workspace is not in them (or whose list failed) shows the raw id. Grants
 * are created for a whole workspace only; team-level grants made elsewhere
 * still show and can be revoked.
 */
export default function OrgScopeGrantsPage() {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const { organization, has } = useOrgAccess();
  const canManage = has("organization:member:manage");
  const grantsQuery = useScopeGrants(organization.id);
  const membersQuery = useOrgMembers(organization.id);
  const workspacesQuery = useOrgWorkspaces(organization.id);
  const gone = useOrgGone(grantsQuery.error, membersQuery.error, workspacesQuery.error);
  const [adding, setAdding] = useState(false);
  const [revoking, setRevoking] = useState<ScopeGrantRow | null>(null);

  const members = useMemo(() => membersQuery.data?.items ?? [], [membersQuery.data]);
  const workspaces = useMemo(() => workspacesQuery.data?.items ?? [], [workspacesQuery.data]);
  const rows = useMemo(
    () => scopeGrantRows(grantsQuery.data?.items ?? [], members, workspaces),
    [grantsQuery.data, members, workspaces],
  );

  if (gone) return <OrgNoAccess />;

  // The names wait for their lists, so rows do not flash raw ids first.
  const pending = grantsQuery.isPending || membersQuery.isPending || workspacesQuery.isPending;

  return (
    <div className="platform-page space-y-5">
      <OrgPageHeader
        eyebrow={organization.name}
        name="eyebrow"
        title={copy.grantsTitle}
        actions={
          canManage ? (
            <Button size="sm" variant="primary" onPress={() => setAdding(true)}>
              <Plus aria-hidden="true" size={15} />
              {copy.addGrant}
            </Button>
          ) : undefined
        }
      />
      <p className="m-0 text-sm text-muted">{copy.grantsIntro}</p>

      <Card<"section"> render={(props) => <section {...props} />} className="min-w-0">
        <Card.Content className="min-w-0" aria-busy={pending || undefined}>
          {pending ? (
            <LoadingState label={copy.loadingGrants} />
          ) : grantsQuery.isError ? (
            <OrgReadError error={grantsQuery.error} fallback={copy.grantsFailed} onRetry={() => void grantsQuery.refetch()} />
          ) : rows.length === 0 ? (
            <EmptyState label={copy.noGrants} />
          ) : (
            <Table className="min-w-0">
              <Table.ScrollContainer
                role="region"
                tabIndex={0}
                aria-label={copy.tableScrolls(copy.grantsLabel)}
                className="platform-members-table-scroll"
              >
                <Table.Content aria-label={copy.grantsLabel}>
                  <Table.Header>
                    <Table.Column id="member" isRowHeader>
                      {copy.columnMember}
                    </Table.Column>
                    <Table.Column id="workspace">{copy.columnWorkspace}</Table.Column>
                    <Table.Column id="scope">{copy.columnScope}</Table.Column>
                    <Table.Column id="granted">{copy.columnGranted}</Table.Column>
                    <Table.Column id="status">{copy.columnStatus}</Table.Column>
                    <Table.Column id="actions">{copy.columnActions}</Table.Column>
                  </Table.Header>
                  <Table.Body items={rows} dependencies={[lang, canManage]}>
                    {(row: ScopeGrantRow) => (
                      <Table.Row id={row.id}>
                        <Table.Cell>
                          {row.member ? (
                            <span className="flex flex-col">
                              <span className="font-semibold text-foreground" dir="auto">
                                {row.member.display_name}
                              </span>
                              {row.member.email && (
                                <span className="text-xs text-muted" dir="ltr">
                                  {row.member.email}
                                </span>
                              )}
                            </span>
                          ) : (
                            <span dir="ltr" className="font-mono text-xs">
                              {row.membership_id}
                            </span>
                          )}
                        </Table.Cell>
                        <Table.Cell>
                          {row.workspace ? (
                            <span dir="auto">{row.workspace.name}</span>
                          ) : (
                            <span dir="ltr" className="font-mono text-xs">
                              {row.workspace_id}
                            </span>
                          )}
                        </Table.Cell>
                        <Table.Cell>
                          {row.team_id === null ? (
                            copy.wholeWorkspace
                          ) : (
                            <span>
                              {copy.team}{" "}
                              <span dir="ltr" className="font-mono text-xs">
                                {row.team_id}
                              </span>
                            </span>
                          )}
                        </Table.Cell>
                        <Table.Cell>
                          <DayText value={row.created_at} />
                        </Table.Cell>
                        <Table.Cell>
                          <Chip color={row.revoked_at === null ? "success" : "default"} variant="soft" size="sm">
                            <Chip.Label>{row.revoked_at === null ? copy.active : copy.revoked}</Chip.Label>
                          </Chip>
                        </Table.Cell>
                        <Table.Cell>
                          {canManage && row.revoked_at === null && (
                            <Button
                              size="sm"
                              variant="ghost"
                              className="text-danger"
                              aria-label={copy.revokeGrantFor(grantMemberName(row))}
                              onPress={() => setRevoking(row)}
                            >
                              <ShieldMinus aria-hidden="true" size={14} />
                              {copy.revoke}
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

      {canManage && adding && <AddScopeGrantModal onClose={() => setAdding(false)} />}
      {canManage && revoking && <RevokeScopeGrantDialog grant={revoking} onClose={() => setRevoking(null)} />}
    </div>
  );
}
