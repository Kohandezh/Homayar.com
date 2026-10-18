import { useMemo, useState } from "react";
import { Button, Card, Chip, Table } from "@heroui/react";
import { Pencil, Plus, UserMinus } from "lucide-react";

import AddOrgMemberModal from "@/components/org/AddOrgMemberModal";
import EditOrgMemberModal from "@/components/org/EditOrgMemberModal";
import { useOrgAccess, useOrgGone } from "@/components/org/org-access";
import { ORG_COPY, type OrgCopy } from "@/components/org/org-copy";
import { orgMemberStatus, type OrgMemberStatus } from "@/components/org/org-model";
import OrgNoAccess from "@/components/org/OrgNoAccess";
import { DayText, EndDayText, OrgPageHeader, OrgReadError, OrgRoleChip } from "@/components/org/OrgUi";
import RevokeOrgMemberDialog from "@/components/org/RevokeOrgMemberDialog";
import { EmptyState, LoadingState } from "@/components/platform/Ui";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import { useOrgMembers } from "@/lib/org-api";
import { canManageOrgMember } from "@/lib/org-roles";
import type { OrgMember } from "@/types/organizations";

const STATUS: Record<OrgMemberStatus, { color: "success" | "default" | "warning" | "danger"; label: (copy: OrgCopy) => string }> = {
  revoked: { color: "danger", label: (copy) => copy.statusRevoked },
  inactive: { color: "default", label: (copy) => copy.statusInactive },
  active: { color: "success", label: (copy) => copy.statusActive },
  scheduled: { color: "warning", label: (copy) => copy.statusScheduled },
  expired: { color: "default", label: (copy) => copy.statusExpired },
};

/**
 * Organization members with their validity windows (HOM-193). Owners and
 * administrators read it; the write controls exist only for a role with
 * `organization:member:manage`, and the row actions only where the server's
 * manage rule would accept them.
 */
export default function OrgMembersPage() {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const { user } = useAuth();
  const { organization, role, has } = useOrgAccess();
  const canManage = has("organization:member:manage");
  const membersQuery = useOrgMembers(organization.id);
  const gone = useOrgGone(membersQuery.error);
  const members = useMemo(() => membersQuery.data?.items ?? [], [membersQuery.data]);

  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<OrgMember | null>(null);
  const [revoking, setRevoking] = useState<OrgMember | null>(null);

  if (gone) return <OrgNoAccess />;

  const manageable = (member: OrgMember) => canManage && canManageOrgMember(role, user?.id, member);

  return (
    <div className="platform-page space-y-5">
      <OrgPageHeader
        eyebrow={organization.name}
        name="eyebrow"
        title={copy.membersTitle}
        actions={
          canManage ? (
            <Button size="sm" variant="primary" onPress={() => setAdding(true)}>
              <Plus aria-hidden="true" size={15} />
              {copy.addMember}
            </Button>
          ) : undefined
        }
      />

      <Card<"section"> render={(props) => <section {...props} />} className="min-w-0">
        <Card.Content className="min-w-0" aria-busy={membersQuery.isPending || undefined}>
          {membersQuery.isPending ? (
            <LoadingState label={copy.loadingMembers} />
          ) : membersQuery.isError ? (
            <OrgReadError error={membersQuery.error} fallback={copy.membersFailed} onRetry={() => void membersQuery.refetch()} />
          ) : members.length === 0 ? (
            <EmptyState label={copy.noMembers} />
          ) : (
            <Table className="min-w-0">
              <Table.ScrollContainer
                role="region"
                tabIndex={0}
                aria-label={copy.tableScrolls(copy.membersLabel)}
                className="platform-members-table-scroll"
              >
                <Table.Content aria-label={copy.membersLabel}>
                  <Table.Header>
                    <Table.Column id="member" isRowHeader>
                      {copy.columnMember}
                    </Table.Column>
                    <Table.Column id="role">{copy.columnRole}</Table.Column>
                    <Table.Column id="from">{copy.columnValidFrom}</Table.Column>
                    <Table.Column id="until">{copy.columnValidUntil}</Table.Column>
                    <Table.Column id="status">{copy.columnStatus}</Table.Column>
                    <Table.Column id="actions">{copy.columnActions}</Table.Column>
                  </Table.Header>
                  <Table.Body items={members} dependencies={[lang, canManage, role, user?.id]}>
                    {(member: OrgMember) => {
                      const status = STATUS[orgMemberStatus(member)];
                      return (
                        <Table.Row id={member.id}>
                          <Table.Cell>
                            <span className="flex flex-col">
                              <span className="font-semibold text-foreground" dir="auto">
                                {member.display_name}
                              </span>
                              {member.email && (
                                <span className="text-xs text-muted" dir="ltr">
                                  {member.email}
                                </span>
                              )}
                            </span>
                          </Table.Cell>
                          <Table.Cell>
                            <OrgRoleChip role={member.role} />
                          </Table.Cell>
                          <Table.Cell>
                            <DayText value={member.valid_from} />
                          </Table.Cell>
                          <Table.Cell>
                            {member.valid_until ? <EndDayText value={member.valid_until} /> : copy.noEndDate}
                          </Table.Cell>
                          <Table.Cell>
                            <Chip color={status.color} variant="soft" size="sm">
                              <Chip.Label>{status.label(copy)}</Chip.Label>
                            </Chip>
                          </Table.Cell>
                          <Table.Cell>
                            {manageable(member) && (
                              <div className="flex flex-wrap items-center gap-2">
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  aria-label={copy.editMember(member.display_name)}
                                  onPress={() => setEditing(member)}
                                >
                                  <Pencil aria-hidden="true" size={14} />
                                  {copy.edit}
                                </Button>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  className="text-danger"
                                  aria-label={copy.revokeMember(member.display_name)}
                                  onPress={() => setRevoking(member)}
                                >
                                  <UserMinus aria-hidden="true" size={14} />
                                  {copy.revoke}
                                </Button>
                              </div>
                            )}
                          </Table.Cell>
                        </Table.Row>
                      );
                    }}
                  </Table.Body>
                </Table.Content>
              </Table.ScrollContainer>
            </Table>
          )}
        </Card.Content>
      </Card>

      {canManage && adding && <AddOrgMemberModal onClose={() => setAdding(false)} />}
      {canManage && editing && <EditOrgMemberModal member={editing} onClose={() => setEditing(null)} />}
      {canManage && revoking && <RevokeOrgMemberDialog member={revoking} onClose={() => setRevoking(null)} />}
    </div>
  );
}
