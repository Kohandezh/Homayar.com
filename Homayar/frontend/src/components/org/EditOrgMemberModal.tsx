import { useState, type FormEvent } from "react";
import { Button, Checkbox, Label, ListBox, Modal, Select, toast } from "@heroui/react";

import { useOrgAccess } from "@/components/org/org-access";
import { ORG_COPY, ORG_ERROR_CODES } from "@/components/org/org-copy";
import { lastIncludedDay, membershipPatch } from "@/components/org/org-model";
import OrgDateField from "@/components/org/OrgDateField";
import { ErrorState } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { isOrganizationGone, useUpdateOrgMember } from "@/lib/org-api";
import { grantableOrgRoles, orgRoleLabel } from "@/lib/org-roles";
import type { OrgMember } from "@/types/organizations";

/**
 * Changes one membership's role or end date. The PATCH holds only what
 * changed (`membershipPatch`); with no change the save button stays off.
 * Picking a date unticks "No end date", and ticking it empties the date, so
 * the two never disagree.
 */
export default function EditOrgMemberModal({ member, onClose }: { member: OrgMember; onClose: () => void }) {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const access = useOrgAccess();
  const roles = grantableOrgRoles(access.role);
  const mutation = useUpdateOrgMember(access.organization.id);
  const pending = mutation.isPending;
  // A snapshot: a list refresh underneath must not move the starting point.
  const [target] = useState(member);
  const [role, setRole] = useState(target.role);
  const [noEndDate, setNoEndDate] = useState(target.valid_until === null);
  // The field shows the last included day: the stored end is the midnight after it.
  const [untilDay, setUntilDay] = useState(lastIncludedDay(target.valid_until));

  const patch = membershipPatch(target, { role, noEndDate, untilDay });
  // An unticked box with an empty date is not a choice yet: nothing saves
  // until there is a date or the box is ticked again.
  const canSave = Object.keys(patch).length > 0 && (noEndDate || untilDay !== null);

  function changeOpen(open: boolean) {
    if (pending) return;
    if (!open) onClose();
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || !canSave) return;
    mutation.mutate(
      { membershipId: target.id, patch },
      {
        onSuccess: () => {
          onClose();
          toast.success(copy.membershipSaved);
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
              <Modal.Heading>{copy.editHeading}</Modal.Heading>
            </Modal.Header>
            <Modal.Body className="space-y-4 overflow-y-auto">
              <p className="m-0 text-sm text-muted">
                <span dir="auto" className="font-semibold text-foreground">
                  {target.display_name}
                </span>
                {target.email && (
                  <>
                    {" "}
                    <span dir="ltr">{target.email}</span>
                  </>
                )}
              </p>

              <Select
                variant="secondary"
                fullWidth
                value={role}
                isDisabled={pending}
                onChange={(key) => {
                  if (typeof key === "string" && (roles as readonly string[]).includes(key)) setRole(key);
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
                      <ListBox.Item key={candidate} id={candidate} textValue={orgRoleLabel(candidate, lang)}>
                        {orgRoleLabel(candidate, lang)}
                        <ListBox.ItemIndicator />
                      </ListBox.Item>
                    ))}
                  </ListBox>
                </Select.Popover>
              </Select>

              <OrgDateField
                label={copy.validUntil}
                calendarLabel={copy.openCalendar(copy.validUntil)}
                value={untilDay}
                onChange={(day) => {
                  setUntilDay(day);
                  if (day) setNoEndDate(false);
                }}
                isDisabled={pending}
              />
              <Checkbox
                variant="secondary"
                isSelected={noEndDate}
                isReadOnly={pending}
                onChange={(selected) => {
                  setNoEndDate(selected);
                  // Unticking puts back the stored last included day, if any.
                  setUntilDay(selected ? null : lastIncludedDay(target.valid_until));
                }}
              >
                <Checkbox.Content>
                  <Checkbox.Control>
                    <Checkbox.Indicator />
                  </Checkbox.Control>
                  {copy.noEndDate}
                </Checkbox.Content>
              </Checkbox>

              {mutation.isError && !isOrganizationGone(mutation.error) && (
                <ErrorState error={mutation.error} codes={ORG_ERROR_CODES} fallback={copy.saveFailed} />
              )}
            </Modal.Body>
            <Modal.Footer>
              <Button variant="ghost" isDisabled={pending} onPress={() => changeOpen(false)}>
                {copy.cancel}
              </Button>
              <Button type="submit" variant="primary" isDisabled={pending || !canSave} isPending={pending}>
                {copy.saveChanges}
              </Button>
            </Modal.Footer>
          </form>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}
