import { useState, type FormEvent } from "react";
import { Button, FieldError, Input, Label, ListBox, Modal, Select, TextField, toast } from "@heroui/react";
import type { CalendarDate } from "@internationalized/date";

import { useOrgAccess } from "@/components/org/org-access";
import { ORG_COPY, ORG_ERROR_CODES } from "@/components/org/org-copy";
import { dayEndIso, dayStartIso, isWindowInvalid } from "@/components/org/org-model";
import OrgDateField from "@/components/org/OrgDateField";
import { ErrorState } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { isOrganizationGone, useGrantOrgMember } from "@/lib/org-api";
import { grantableOrgRoles, orgRoleLabel } from "@/lib/org-roles";
import type { GrantableOrganizationRole, OrgMemberGrantInput } from "@/types/organizations";

/**
 * Grants an org role to an existing, verified account. The Role select holds
 * only the roles the actor's org role may grant. The body has only the fields
 * that are set. Mounted only while open, so every open starts clean; a
 * refusal keeps the dialog open with its reason inside.
 */
export default function AddOrgMemberModal({ onClose }: { onClose: () => void }) {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const access = useOrgAccess();
  const roles = grantableOrgRoles(access.role);
  const mutation = useGrantOrgMember(access.organization.id);
  const pending = mutation.isPending;
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<GrantableOrganizationRole>(roles.includes("analyst") ? "analyst" : roles[0]);
  const [fromDay, setFromDay] = useState<CalendarDate | null>(null);
  const [untilDay, setUntilDay] = useState<CalendarDate | null>(null);
  // The email error only speaks after a submit attempt.
  const [submitted, setSubmitted] = useState(false);

  const trimmed = email.trim();
  const windowInvalid = isWindowInvalid(fromDay, untilDay);

  function changeOpen(open: boolean) {
    if (pending) return;
    if (!open) onClose();
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setSubmitted(true);
    if (!trimmed || windowInvalid || !role) return;
    const input: OrgMemberGrantInput = { email: trimmed, role };
    // The start day starts at its midnight; the end day is included, so it
    // ends at the next midnight.
    if (fromDay) input.valid_from = dayStartIso(fromDay);
    if (untilDay) input.valid_until = dayEndIso(untilDay);
    mutation.mutate(input, {
      onSuccess: () => {
        onClose();
        toast.success(copy.memberAdded);
      },
      onError: (error) => {
        access.reportError(error);
      },
    });
  }

  return (
    <Modal.Backdrop isOpen onOpenChange={changeOpen}>
      <Modal.Container>
        <Modal.Dialog>
          <form onSubmit={submit} noValidate className="flex min-h-0 flex-1 flex-col">
            <Modal.Header>
              <Modal.Heading>{copy.addMemberHeading}</Modal.Heading>
            </Modal.Header>
            <Modal.Body className="space-y-4 overflow-y-auto">
              <TextField
                variant="secondary"
                type="email"
                value={email}
                onChange={setEmail}
                isRequired
                isInvalid={submitted && !trimmed}
                isReadOnly={pending}
                fullWidth
              >
                <Label>{copy.email}</Label>
                <Input dir="ltr" autoComplete="off" />
                <FieldError>{copy.emailRequired}</FieldError>
              </TextField>

              <Select
                variant="secondary"
                fullWidth
                value={role}
                isDisabled={pending}
                onChange={(key) => {
                  if (typeof key === "string" && (roles as readonly string[]).includes(key)) {
                    setRole(key as GrantableOrganizationRole);
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
                      <ListBox.Item key={candidate} id={candidate} textValue={orgRoleLabel(candidate, lang)}>
                        {orgRoleLabel(candidate, lang)}
                        <ListBox.ItemIndicator />
                      </ListBox.Item>
                    ))}
                  </ListBox>
                </Select.Popover>
              </Select>

              <div className="grid gap-3 sm:grid-cols-2">
                <OrgDateField
                  label={copy.validFrom}
                  calendarLabel={copy.openCalendar(copy.validFrom)}
                  description={copy.optional}
                  value={fromDay}
                  onChange={setFromDay}
                  isDisabled={pending}
                />
                <OrgDateField
                  label={copy.validUntil}
                  calendarLabel={copy.openCalendar(copy.validUntil)}
                  description={copy.optional}
                  value={untilDay}
                  onChange={setUntilDay}
                  isDisabled={pending}
                />
              </div>
              {windowInvalid && (
                <p className="m-0 platform-form-error" role="alert">
                  {copy.windowInvalid}
                </p>
              )}

              {mutation.isError && !isOrganizationGone(mutation.error) && (
                <ErrorState error={mutation.error} codes={ORG_ERROR_CODES} fallback={copy.addMemberFailed} />
              )}
            </Modal.Body>
            <Modal.Footer>
              <Button variant="ghost" isDisabled={pending} onPress={() => changeOpen(false)}>
                {copy.cancel}
              </Button>
              <Button type="submit" variant="primary" isDisabled={pending} isPending={pending}>
                {copy.addMember}
              </Button>
            </Modal.Footer>
          </form>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}
