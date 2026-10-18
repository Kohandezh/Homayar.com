import type { ComponentProps } from "react";
import { Calendar, DateField, DatePicker, Description, Label } from "@heroui/react";
import type { CalendarDate } from "@internationalized/date";

type DateSegment = ComponentProps<typeof DateField.Segment>["segment"];

/**
 * One optional day. A HeroUI DatePicker reads the locale of the console's
 * I18nProvider (`fa-IR` / `en-US`), so a Persian page types and picks the
 * day in the same calendar it displays, as AssignMemberForm.tsx does.
 * `org-model.ts` turns the day into what the API takes.
 */
export default function OrgDateField({
  label,
  calendarLabel,
  description,
  value,
  onChange,
  isDisabled,
}: {
  label: string;
  /** The name of the button that opens the calendar. */
  calendarLabel: string;
  description?: string;
  value: CalendarDate | null;
  onChange: (day: CalendarDate | null) => void;
  isDisabled?: boolean;
}) {
  return (
    <DatePicker value={value} onChange={onChange} isDisabled={isDisabled} className="w-full">
      <Label>{label}</Label>
      <DateField.Group variant="secondary" fullWidth>
        <DateField.Input>{(segment: DateSegment) => <DateField.Segment segment={segment} />}</DateField.Input>
        <DateField.Suffix>
          <DatePicker.Trigger aria-label={calendarLabel}>
            <DatePicker.TriggerIndicator />
          </DatePicker.Trigger>
        </DateField.Suffix>
      </DateField.Group>
      {description && <Description>{description}</Description>}
      <DatePicker.Popover>
        <Calendar aria-label={label}>
          <Calendar.Header>
            <Calendar.YearPickerTrigger>
              <Calendar.YearPickerTriggerHeading />
              <Calendar.YearPickerTriggerIndicator />
            </Calendar.YearPickerTrigger>
            <Calendar.NavButton slot="previous" />
            <Calendar.NavButton slot="next" />
          </Calendar.Header>
          <Calendar.Grid>
            <Calendar.GridHeader>{(day) => <Calendar.HeaderCell>{day}</Calendar.HeaderCell>}</Calendar.GridHeader>
            <Calendar.GridBody>{(date) => <Calendar.Cell date={date} />}</Calendar.GridBody>
          </Calendar.Grid>
          <Calendar.YearPickerGrid>
            <Calendar.YearPickerGridBody>{({ year }) => <Calendar.YearPickerCell year={year} />}</Calendar.YearPickerGridBody>
          </Calendar.YearPickerGrid>
        </Calendar>
      </DatePicker.Popover>
    </DatePicker>
  );
}
