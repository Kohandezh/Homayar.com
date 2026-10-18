import { useState, type ComponentProps, type FormEvent } from "react";
import {
  Button,
  ComboBox,
  DateField,
  DateRangePicker,
  Description,
  Input,
  Label,
  ListBox,
  RangeCalendar,
  Select,
  toast,
} from "@heroui/react";
import type { CalendarDate } from "@internationalized/date";
import { UserPlus } from "lucide-react";

import { ErrorState } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { useAssignTeamMember, useMembers } from "@/lib/platform-api";
import type { AssignTeamMemberInput, TeamRole } from "@/types/platform";
import { TEAM_ERROR_CODES, TEAMS_COPY, teamRoleLabel } from "@/components/admin/teams/teams-copy";
import { assignmentDatesFromRange } from "@/components/admin/teams/team-model";

type DateSegment = ComponentProps<typeof DateField.Segment>["segment"];

interface DateRange {
  start: CalendarDate;
  end: CalendarDate;
}

const TEAM_ROLES: TeamRole[] = ["agent", "supervisor"];

/**
 * The assign form at the bottom of a team's drawer. It mounts with the
 * drawer, so `GET /members` is only asked for once a team is actually open.
 * A successful assign resets the form; a refused one keeps every choice.
 */
export default function AssignMemberForm({ teamId }: { teamId: string }) {
  const { lang } = useI18n();
  const copy = TEAMS_COPY[lang];
  const membersQuery = useMembers();
  const assignMutation = useAssignTeamMember();
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const [role, setRole] = useState<TeamRole>("agent");
  const [range, setRange] = useState<DateRange | null>(null);
  const pending = assignMutation.isPending;
  const members = membersQuery.data?.items ?? [];

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || selectedUserId === null) return;
    const input: AssignTeamMemberInput = { user_id: selectedUserId, role };
    if (range) {
      const dates = assignmentDatesFromRange(range.start, range.end);
      input.valid_from = dates.valid_from;
      input.valid_until = dates.valid_until;
    }
    assignMutation.reset();
    assignMutation.mutate(
      { teamId, input },
      {
        onSuccess: () => {
          toast.success(copy.memberAssigned);
          setSelectedUserId(null);
          setRole("agent");
          setRange(null);
        },
      },
    );
  }

  return (
    <form onSubmit={submit} noValidate className="mt-6 border-t border-separator pt-4">
      <h3 className="m-0 mb-3 text-sm font-semibold">{copy.assignHeading}</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <ComboBox
          className="w-full"
          selectedKey={selectedUserId}
          onSelectionChange={(key) => setSelectedUserId(typeof key === "string" ? key : null)}
          isDisabled={pending}
          fullWidth
        >
          <Label>{copy.memberLabel}</Label>
          <ComboBox.InputGroup>
            <Input placeholder={copy.memberPlaceholder} />
            <ComboBox.Trigger />
          </ComboBox.InputGroup>
          <ComboBox.Popover>
            <ListBox>
              {members.map((member) => (
                <ListBox.Item key={member.user_id} id={member.user_id} textValue={member.display_name}>
                  <span dir="auto">{member.display_name}</span>
                  {member.email && <Description>{member.email}</Description>}
                  <ListBox.ItemIndicator />
                </ListBox.Item>
              ))}
            </ListBox>
          </ComboBox.Popover>
        </ComboBox>

        <Select
          className="w-full"
          aria-label={copy.roleLabel}
          value={role}
          isDisabled={pending}
          onChange={(next) => {
            if (typeof next === "string") setRole(next as TeamRole);
          }}
        >
          <Label>{copy.roleLabel}</Label>
          <Select.Trigger>
            <Select.Value />
            <Select.Indicator />
          </Select.Trigger>
          <Select.Popover>
            <ListBox>
              {TEAM_ROLES.map((candidate) => (
                <ListBox.Item key={candidate} id={candidate} textValue={teamRoleLabel(candidate, lang)}>
                  {teamRoleLabel(candidate, lang)}
                  <ListBox.ItemIndicator />
                </ListBox.Item>
              ))}
            </ListBox>
          </Select.Popover>
        </Select>
      </div>

      <DateRangePicker value={range} onChange={setRange} className="mt-3 w-full sm:w-auto" isDisabled={pending}>
        <Label>{copy.validPeriod}</Label>
        <DateField.Group variant="secondary">
          <DateField.InputContainer>
            <DateField.Input slot="start">
              {(segment: DateSegment) => <DateField.Segment segment={segment} />}
            </DateField.Input>
            <DateRangePicker.RangeSeparator />
            <DateField.Input slot="end">
              {(segment: DateSegment) => <DateField.Segment segment={segment} />}
            </DateField.Input>
          </DateField.InputContainer>
          <DateField.Suffix>
            <DateRangePicker.Trigger aria-label={copy.chooseRange}>
              <DateRangePicker.TriggerIndicator />
            </DateRangePicker.Trigger>
          </DateField.Suffix>
        </DateField.Group>
        <DateRangePicker.Popover>
          <RangeCalendar>
            <RangeCalendar.Header>
              <RangeCalendar.NavButton slot="previous" />
              <RangeCalendar.Heading />
              <RangeCalendar.NavButton slot="next" />
            </RangeCalendar.Header>
            <RangeCalendar.Grid>
              <RangeCalendar.GridHeader>
                {(day) => <RangeCalendar.HeaderCell>{day}</RangeCalendar.HeaderCell>}
              </RangeCalendar.GridHeader>
              <RangeCalendar.GridBody>
                {(date) => <RangeCalendar.Cell date={date} />}
              </RangeCalendar.GridBody>
            </RangeCalendar.Grid>
          </RangeCalendar>
        </DateRangePicker.Popover>
      </DateRangePicker>

      {membersQuery.isError && (
        <div className="mt-3">
          <ErrorState
            error={membersQuery.error}
            codes={TEAM_ERROR_CODES}
            fallback={copy.membersFailed}
          />
        </div>
      )}
      {assignMutation.isError && (
        <div className="mt-3">
          <ErrorState error={assignMutation.error} codes={TEAM_ERROR_CODES} fallback={copy.assignFailed} />
        </div>
      )}

      <Button
        type="submit"
        variant="primary"
        className="mt-3"
        isDisabled={pending || selectedUserId === null || membersQuery.isError}
      >
        <UserPlus aria-hidden="true" size={15} />
        {copy.assign}
      </Button>
    </form>
  );
}
