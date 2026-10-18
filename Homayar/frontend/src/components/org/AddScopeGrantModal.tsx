import { useState, type FormEvent } from "react";
import { Button, Description, Label, ListBox, Modal, Select, toast } from "@heroui/react";

import { useOrgAccess, useOrgGone } from "@/components/org/org-access";
import { ORG_COPY, ORG_ERROR_CODES } from "@/components/org/org-copy";
import { ErrorState, LoadingState } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { isOrganizationGone, useCreateScopeGrant, useOrgMembers, useOrgWorkspaces } from "@/lib/org-api";
import { orgRoleLabel } from "@/lib/org-roles";
import type { OrgMember } from "@/types/organizations";

/** Only an effective analyst or executive can get a scope grant (the server checks it too). */
function isGrantable(member: OrgMember) {
  return member.is_effective && (member.role === "analyst" || member.role === "executive");
}

/**
 * Gives one analyst or executive one whole workspace. The body never holds a
 * `team_id`: no organization route lists a workspace's teams. The two lists
 * are the page's cached reads.
 */
export default function AddScopeGrantModal({ onClose }: { onClose: () => void }) {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const access = useOrgAccess();
  const membersQuery = useOrgMembers(access.organization.id);
  const workspacesQuery = useOrgWorkspaces(access.organization.id);
  useOrgGone(membersQuery.error, workspacesQuery.error);
  const mutation = useCreateScopeGrant(access.organization.id);
  const pending = mutation.isPending;
  const [membershipId, setMembershipId] = useState<string | null>(null);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const eligible = (membersQuery.data?.items ?? []).filter(isGrantable);
  const workspaces = workspacesQuery.data?.items ?? [];
  const listsPending = membersQuery.isPending || workspacesQuery.isPending;
  const listError = membersQuery.error ?? workspacesQuery.error;
  const ready = !listsPending && !listError && eligible.length > 0;

  function changeOpen(open: boolean) {
    if (pending) return;
    if (!open) onClose();
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || !membershipId || !workspaceId) return;
    mutation.mutate(
      { membership_id: membershipId, workspace_id: workspaceId },
      {
        onSuccess: () => {
          onClose();
          toast.success(copy.scopeGranted);
        },
        onError: (error) => {
          access.reportError(error);
        },
      },
    );
  }

  return (
    <Modal.Backdrop isOpen onOpenChange={changeOpen}>
      <Modal.Container>
        <Modal.Dialog>
          <form onSubmit={submit} noValidate className="flex min-h-0 flex-1 flex-col">
            <Modal.Header>
              <Modal.Heading>{copy.addGrantHeading}</Modal.Heading>
            </Modal.Header>
            <Modal.Body className="space-y-4 overflow-y-auto">
              {listsPending ? (
                <LoadingState label={copy.loadingMembers} />
              ) : listError ? (
                !isOrganizationGone(listError) && (
                  <ErrorState error={listError} codes={ORG_ERROR_CODES} fallback={copy.membersFailed} />
                )
              ) : eligible.length === 0 ? (
                <p className="m-0 text-sm text-muted">{copy.noEligible}</p>
              ) : (
                <>
                  <Select
                    variant="secondary"
                    fullWidth
                    placeholder={copy.chooseMember}
                    value={membershipId}
                    isDisabled={pending}
                    onChange={(key) => setMembershipId(typeof key === "string" ? key : null)}
                  >
                    <Label>{copy.member}</Label>
                    <Select.Trigger>
                      <Select.Value />
                      <Select.Indicator />
                    </Select.Trigger>
                    <Select.Popover>
                      <ListBox>
                        {eligible.map((member) => (
                          <ListBox.Item key={member.id} id={member.id} textValue={member.display_name}>
                            <Label dir="auto">{member.display_name}</Label>
                            <Description>
                              {orgRoleLabel(member.role, lang)}
                              {member.email && (
                                <>
                                  {" · "}
                                  <span dir="ltr">{member.email}</span>
                                </>
                              )}
                            </Description>
                            <ListBox.ItemIndicator />
                          </ListBox.Item>
                        ))}
                      </ListBox>
                    </Select.Popover>
                  </Select>

                  <Select
                    variant="secondary"
                    fullWidth
                    placeholder={copy.chooseWorkspace}
                    value={workspaceId}
                    isDisabled={pending}
                    onChange={(key) => setWorkspaceId(typeof key === "string" ? key : null)}
                  >
                    <Label>{copy.workspace}</Label>
                    <Select.Trigger>
                      <Select.Value />
                      <Select.Indicator />
                    </Select.Trigger>
                    <Select.Popover>
                      <ListBox>
                        {workspaces.map((workspace) => (
                          <ListBox.Item key={workspace.id} id={workspace.id} textValue={workspace.name}>
                            <span dir="auto">{workspace.name}</span>
                            <ListBox.ItemIndicator />
                          </ListBox.Item>
                        ))}
                      </ListBox>
                    </Select.Popover>
                  </Select>
                </>
              )}

              {mutation.isError && !isOrganizationGone(mutation.error) && (
                <ErrorState error={mutation.error} codes={ORG_ERROR_CODES} fallback={copy.grantFailed} />
              )}
            </Modal.Body>
            <Modal.Footer>
              <Button variant="ghost" isDisabled={pending} onPress={() => changeOpen(false)}>
                {copy.cancel}
              </Button>
              {ready && (
                <Button
                  type="submit"
                  variant="primary"
                  isDisabled={pending || !membershipId || !workspaceId}
                  isPending={pending}
                >
                  {copy.grantScope}
                </Button>
              )}
            </Modal.Footer>
          </form>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}
