import { useState, type FormEvent } from "react";
import { Button, Description, Label, ListBox, Modal, Select, toast } from "@heroui/react";

import { useOrgAccess, useOrgGone } from "@/components/org/org-access";
import { ORG_COPY, ORG_ERROR_CODES } from "@/components/org/org-copy";
import { ErrorState, LoadingState } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { isOrganizationGone, useInstallWorkspaceMember, useOrgMembers } from "@/lib/org-api";
import { delegatedWorkspaceRoles } from "@/lib/org-roles";
import { roleLabel } from "@/lib/roles";
import type { DelegatedWorkspaceRole, OrgWorkspace } from "@/types/organizations";

/**
 * Delegated install: gives an effective organization member a role in one
 * workspace. The member list is read when the dialog opens, so the
 * workspaces page itself never asks for it.
 */
export default function InstallWorkspaceMemberModal({
  workspace,
  onClose,
}: {
  workspace: OrgWorkspace;
  onClose: () => void;
}) {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const access = useOrgAccess();
  const membersQuery = useOrgMembers(access.organization.id);
  const mutation = useInstallWorkspaceMember(access.organization.id);
  const pending = mutation.isPending;
  const roles = delegatedWorkspaceRoles(access.role);
  // A gone organization swaps the whole page for the no-access state.
  useOrgGone(membersQuery.error);
  const [target] = useState(workspace);
  const [userId, setUserId] = useState<string | null>(null);
  const [role, setRole] = useState<DelegatedWorkspaceRole | null>(null);

  const effective = (membersQuery.data?.items ?? []).filter((member) => member.is_effective);
  const chosen = effective.find((member) => member.user_id === userId) ?? null;

  function changeOpen(open: boolean) {
    if (pending) return;
    if (!open) onClose();
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || !chosen || !role) return;
    mutation.mutate(
      { workspaceId: target.id, input: { user_id: chosen.user_id, role } },
      {
        onSuccess: () => {
          onClose();
          toast.success(copy.installed(chosen.display_name, target.name));
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
              <Modal.Heading>{copy.installHeading}</Modal.Heading>
            </Modal.Header>
            <Modal.Body className="space-y-4 overflow-y-auto">
              <p className="m-0 text-sm font-semibold text-foreground" dir="auto">
                {target.name}
              </p>
              {membersQuery.isPending ? (
                <LoadingState label={copy.loadingMembers} />
              ) : membersQuery.isError ? (
                !isOrganizationGone(membersQuery.error) && (
                  <ErrorState error={membersQuery.error} codes={ORG_ERROR_CODES} fallback={copy.membersFailed} />
                )
              ) : effective.length === 0 ? (
                <p className="m-0 text-sm text-muted">{copy.noActiveMembers}</p>
              ) : (
                <>
                  <Select
                    variant="secondary"
                    fullWidth
                    placeholder={copy.chooseMember}
                    value={userId}
                    isDisabled={pending}
                    onChange={(key) => setUserId(typeof key === "string" ? key : null)}
                  >
                    <Label>{copy.member}</Label>
                    <Select.Trigger>
                      <Select.Value />
                      <Select.Indicator />
                    </Select.Trigger>
                    <Select.Popover>
                      <ListBox>
                        {effective.map((member) => (
                          <ListBox.Item key={member.user_id} id={member.user_id} textValue={member.display_name}>
                            <Label dir="auto">{member.display_name}</Label>
                            {member.email && <Description dir="ltr">{member.email}</Description>}
                            <ListBox.ItemIndicator />
                          </ListBox.Item>
                        ))}
                      </ListBox>
                    </Select.Popover>
                  </Select>

                  <Select
                    variant="secondary"
                    fullWidth
                    placeholder={copy.chooseRole}
                    value={role}
                    isDisabled={pending}
                    onChange={(key) => {
                      if (typeof key === "string" && (roles as readonly string[]).includes(key)) {
                        setRole(key as DelegatedWorkspaceRole);
                      }
                    }}
                  >
                    <Label>{copy.role}</Label>
                    <Select.Trigger>
                      <Select.Value />
                      <Select.Indicator />
                    </Select.Trigger>
                    <Select.Popover>
                      <ListBox>
                        {roles.map((candidate) => (
                          <ListBox.Item key={candidate} id={candidate} textValue={roleLabel(candidate, lang)}>
                            {roleLabel(candidate, lang)}
                            <ListBox.ItemIndicator />
                          </ListBox.Item>
                        ))}
                      </ListBox>
                    </Select.Popover>
                  </Select>
                </>
              )}

              {mutation.isError && !isOrganizationGone(mutation.error) && (
                <ErrorState error={mutation.error} codes={ORG_ERROR_CODES} fallback={copy.installFailed} />
              )}
            </Modal.Body>
            <Modal.Footer>
              <Button variant="ghost" isDisabled={pending} onPress={() => changeOpen(false)}>
                {copy.cancel}
              </Button>
              <Button type="submit" variant="primary" isDisabled={pending || !chosen || !role} isPending={pending}>
                {copy.addMember}
              </Button>
            </Modal.Footer>
          </form>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}
