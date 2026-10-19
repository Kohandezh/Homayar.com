import { useState } from "react";
import { AlertDialog, Button, Chip, Drawer, Table, toast } from "@heroui/react";
import { UserMinus } from "lucide-react";

import { EmptyState, ErrorState, LoadingState } from "@/components/platform/Ui";
import { formatDay } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useRevokeTeamAssignment, useTeam } from "@/lib/platform-api";
import type { Team, TeamAssignment } from "@/types/platform";
import AssignMemberForm from "@/components/admin/teams/AssignMemberForm";
import { assignmentStatus, isRevocable, type AssignmentStatus } from "@/components/admin/teams/team-model";
import { TEAM_ERROR_CODES, TEAMS_COPY, teamRoleLabel } from "@/components/admin/teams/teams-copy";

const STATUS_COLORS: Record<AssignmentStatus, "success" | "warning" | "default"> = {
  effective: "success",
  starts_later: "warning",
  revoked: "default",
  ended: "default",
};

function statusText(status: AssignmentStatus, lang: "fa" | "en") {
  const copy = TEAMS_COPY[lang];
  if (status === "revoked") return copy.revoked;
  if (status === "effective") return copy.effective;
  if (status === "starts_later") return copy.startsLater;
  return copy.ended;
}

function AssignmentStatusChip({ status, lang }: { status: AssignmentStatus; lang: "fa" | "en" }) {
  return (
    <Chip color={STATUS_COLORS[status]} variant="soft" size="sm">
      <Chip.Label>{statusText(status, lang)}</Chip.Label>
    </Chip>
  );
}

/** One assignments row: who, as what, for how long, and where that stands now. */
function AssignmentRow({
  assignment,
  canManage,
  lang,
  onRevoke,
}: {
  assignment: TeamAssignment;
  canManage: boolean;
  lang: "fa" | "en";
  onRevoke: (assignment: TeamAssignment) => void;
}) {
  const copy = TEAMS_COPY[lang];
  const status = assignmentStatus(assignment);
  const role = teamRoleLabel(assignment.role, lang);
  return (
    <Table.Row id={assignment.id}>
      <Table.Cell>
        <span className="block font-semibold text-foreground" dir="auto">
          {assignment.display_name}
        </span>
        {assignment.email && (
          <span className="mt-0.5 block truncate text-xs text-muted" dir="ltr">
            {assignment.email}
          </span>
        )}
      </Table.Cell>
      <Table.Cell className="whitespace-nowrap text-xs text-muted">{role}</Table.Cell>
      <Table.Cell className="whitespace-nowrap text-xs text-muted">{formatDay(assignment.valid_from, lang)}</Table.Cell>
      <Table.Cell className="whitespace-nowrap text-xs text-muted">
        {assignment.valid_until ? formatDay(assignment.valid_until, lang) : copy.noEndDate}
      </Table.Cell>
      <Table.Cell>
        <AssignmentStatusChip status={status} lang={lang} />
      </Table.Cell>
      {canManage && (
        <Table.Cell>
          {isRevocable(status) ? (
            <Button
              size="sm"
              variant="ghost"
              className="text-danger"
              aria-label={copy.revokeAssignment(assignment.display_name, role)}
              onPress={() => onRevoke(assignment)}
            >
              <UserMinus aria-hidden="true" size={14} />
              {copy.revoke}
            </Button>
          ) : (
            <span className="text-xs text-muted" aria-hidden="true">
              —
            </span>
          )}
        </Table.Cell>
      )}
    </Table.Row>
  );
}

/**
 * One team's drawer: its status, its assignment history and, for managers,
 * the assign form. The detail query shares its cache entry with the member
 * counts on the page, so opening a row is usually instant.
 */
export default function TeamDrawer({
  team,
  canManage,
  onClose,
}: {
  team: Team | null;
  canManage: boolean;
  onClose: () => void;
}) {
  const { lang, dir } = useI18n();
  const copy = TEAMS_COPY[lang];
  const detailQuery = useTeam(team?.id);
  const revokeMutation = useRevokeTeamAssignment();
  const [revokeTarget, setRevokeTarget] = useState<TeamAssignment | null>(null);
  const assignments = detailQuery.data?.assignments ?? [];

  async function confirmRevoke() {
    if (!team || !revokeTarget || revokeMutation.isPending) return;
    try {
      await revokeMutation.mutateAsync({ teamId: team.id, assignmentId: revokeTarget.id });
      toast.success(copy.assignmentRevoked);
      closeRevoke();
    } catch {
      // The alert inside the dialog shows the reason.
    }
  }

  /**
   * Closing always clears the error too: the mutation outlives the
   * confirmation, and the next row's question must not arrive pre-answered.
   */
  function closeRevoke() {
    revokeMutation.reset();
    setRevokeTarget(null);
  }

  /** Every confirmation starts clean, whatever the previous one ended in. */
  function openRevoke(assignment: TeamAssignment) {
    revokeMutation.reset();
    setRevokeTarget(assignment);
  }

  return (
    <>
      <Drawer.Backdrop
        isOpen={team !== null}
        onOpenChange={(open) => {
          if (!open) onClose();
        }}
      >
        <Drawer.Content placement={dir === "rtl" ? "left" : "right"} className="justify-end">
          {/* Wide on a desktop so all six columns fit; full width on a phone,
              where the table scrolls inside its own container instead. */}
          <Drawer.Dialog
            aria-label={team?.name}
            className="w-full p-4 sm:w-[30rem] lg:w-[48rem]"
          >
            <Drawer.CloseTrigger aria-label={copy.close} />
            {team && (
              <>
                <Drawer.Header>
                  <Drawer.Heading level={2}>
                    {/* User content: no forced direction, the browser reads the
                      name's own order. */}
                    <bdi>{team.name}</bdi>
                  </Drawer.Heading>
                  <Chip color={team.is_active ? "success" : "default"} variant="soft" size="sm" className="ms-2">
                    <Chip.Label>{team.is_active ? copy.active : copy.inactive}</Chip.Label>
                  </Chip>
                </Drawer.Header>
                <Drawer.Body>
                  {detailQuery.isPending ? (
                    <LoadingState label={copy.loading} />
                  ) : detailQuery.isError ? (
                    <ErrorState
                      error={detailQuery.error}
                      codes={TEAM_ERROR_CODES}
                      fallback={copy.detailLoadFailed}
                    />
                  ) : (
                    <>
                      <Table className="min-w-0">
                        <Table.ScrollContainer
                          role="region"
                          tabIndex={0}
                          aria-label={copy.assignmentsScrollLabel}
                          className="platform-members-table-scroll"
                        >
                          <Table.Content aria-label={copy.assignmentsLabel}>
                          <Table.Header>
                            <Table.Column id="member" isRowHeader>
                              {copy.columnMember}
                            </Table.Column>
                            <Table.Column id="role">{copy.columnRole}</Table.Column>
                            <Table.Column id="from">{copy.columnFrom}</Table.Column>
                            <Table.Column id="until">{copy.columnUntil}</Table.Column>
                            <Table.Column id="status">{copy.columnStatus}</Table.Column>
                            {canManage && <Table.Column id="actions">{copy.columnActions}</Table.Column>}
                          </Table.Header>
                          <Table.Body
                            items={assignments}
                            dependencies={[lang, canManage, revokeMutation.isPending]}
                            renderEmptyState={() => <EmptyState label={copy.noAssignments} />}
                          >
                            {(assignment: TeamAssignment) => (
                              <AssignmentRow
                                assignment={assignment}
                                canManage={canManage}
                                lang={lang}
                                onRevoke={openRevoke}
                              />
                            )}
                          </Table.Body>
                        </Table.Content>
                        </Table.ScrollContainer>
                      </Table>
                      {canManage && <AssignMemberForm teamId={team.id} />}
                    </>
                  )}
                </Drawer.Body>
              </>
            )}
          </Drawer.Dialog>
        </Drawer.Content>
      </Drawer.Backdrop>
      {/* The revoke question sits above the drawer it came from. */}
      {team && revokeTarget && (
        <AlertDialog
          isOpen
          onOpenChange={(open) => {
            if (!revokeMutation.isPending && !open) closeRevoke();
          }}
        >
          <AlertDialog.Backdrop>
            <AlertDialog.Container>
              <AlertDialog.Dialog>
                <AlertDialog.Header>
                  <AlertDialog.Icon status="danger" />
                  <AlertDialog.Heading>{copy.revokeHeading}</AlertDialog.Heading>
                </AlertDialog.Header>
                <AlertDialog.Body className="space-y-3">
                  <p className="m-0 text-sm text-muted" dir="auto">
                    {copy.revokeBody(
                      revokeTarget.display_name,
                      teamRoleLabel(revokeTarget.role, lang).toLowerCase(),
                      team.name,
                    )}
                  </p>
                  {revokeMutation.isError && (
                    <ErrorState
                      error={revokeMutation.error}
                      codes={TEAM_ERROR_CODES}
                      fallback={copy.revokeFailed}
                    />
                  )}
                </AlertDialog.Body>
                <AlertDialog.Footer>
                  <Button variant="ghost" isDisabled={revokeMutation.isPending} onPress={closeRevoke}>
                    {copy.cancel}
                  </Button>
                  <Button variant="danger" isDisabled={revokeMutation.isPending} onPress={() => void confirmRevoke()}>
                    <UserMinus aria-hidden="true" size={15} />
                    {copy.revokeConfirm}
                  </Button>
                </AlertDialog.Footer>
              </AlertDialog.Dialog>
            </AlertDialog.Container>
          </AlertDialog.Backdrop>
        </AlertDialog>
      )}
    </>
  );
}
