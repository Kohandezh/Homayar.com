import type { ComponentProps, Key, ReactNode } from "react";
import {
  Button,
  DateField,
  DateRangePicker,
  Header,
  Label,
  ListBox,
  RangeCalendar,
  Select,
} from "@heroui/react";
import { FilterX } from "lucide-react";
import type { CalendarDate } from "@internationalized/date";

import {
  auditEventGroupLabel,
  auditEventLabel,
  auditEventTypeGroups,
  auditTargetLabel,
  auditTargetTypes,
} from "@/lib/audit-labels";
import {
  hasAuditFilters,
  NO_AUDIT_FILTERS,
  parseAuditDay,
  type AuditFilters,
} from "@/lib/audit-filters";
import { useI18n } from "@/lib/i18n-context";
import { statusLabel } from "@/lib/status-labels";
import type { WorkspaceMember } from "@/types/platform";

/** The outcomes the backend records (backend/services/audit_service.py). */
const OUTCOMES = ["success", "failure"];

type DateSegment = ComponentProps<typeof DateField.Segment>["segment"];

interface DateRange {
  start: CalendarDate;
  end: CalendarDate;
}

function memberName(member: WorkspaceMember): string {
  return member.display_name || member.email || member.phone_e164 || member.user_id;
}

/**
 * One filter. Nothing selected means "no filter", so the empty value is the
 * cleared state of the select rather than an extra "all" entry.
 */
function FilterSelect({
  label,
  placeholder,
  value,
  onChange,
  children,
  className,
}: {
  label: string;
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Select
      className={className}
      placeholder={placeholder}
      value={value || null}
      onChange={(key: Key | null) => onChange(typeof key === "string" ? key : "")}
      onClear={() => onChange("")}
      variant="secondary"
    >
      <Label>{label}</Label>
      <Select.Trigger>
        <Select.Value />
        <Select.ClearButton />
        <Select.Indicator />
      </Select.Trigger>
      <Select.Popover>
        <ListBox>{children}</ListBox>
      </Select.Popover>
    </Select>
  );
}

/**
 * The filter row of the audit log. It never holds state of its own: every
 * change is reported upwards and ends up in the URL, so the filtered view can
 * be shared and reloaded.
 */
export default function AuditFilterBar({
  filters,
  members,
  onChange,
}: {
  filters: AuditFilters;
  members: WorkspaceMember[];
  onChange: (filters: AuditFilters) => void;
}) {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const start = parseAuditDay(filters.from);
  const end = parseAuditDay(filters.to);
  // React Aria wants both ends or nothing; one lone day is not a range yet.
  const range: DateRange | null = start && end ? { start, end } : null;

  function set(field: keyof AuditFilters, value: string) {
    onChange({ ...filters, [field]: value });
  }

  function setRange(next: DateRange | null) {
    onChange({
      ...filters,
      // `toString()` is always the Gregorian day, whatever calendar is shown.
      from: next ? next.start.toString().slice(0, 10) : "",
      to: next ? next.end.toString().slice(0, 10) : "",
    });
  }

  return (
    <div className="flex flex-wrap items-end gap-3">
      <FilterSelect
        className="w-full sm:w-64"
        label={isFa ? "نوع رویداد" : "Event type"}
        placeholder={isFa ? "همهٔ رویدادها" : "All events"}
        value={filters.eventType}
        onChange={(value) => set("eventType", value)}
      >
        {auditEventTypeGroups().map((group) => (
          <ListBox.Section key={group.prefix}>
            <Header className="px-2 py-1 text-xs text-muted">
              {auditEventGroupLabel(group.prefix, lang)}
            </Header>
            {group.types.map((type) => (
              <ListBox.Item key={type} id={type} textValue={auditEventLabel(type, lang) ?? type}>
                {auditEventLabel(type, lang) ?? type}
                <ListBox.ItemIndicator />
              </ListBox.Item>
            ))}
          </ListBox.Section>
        ))}
      </FilterSelect>

      <FilterSelect
        className="w-full sm:w-40"
        label={isFa ? "نتیجه" : "Outcome"}
        placeholder={isFa ? "همهٔ نتیجه‌ها" : "All outcomes"}
        value={filters.outcome}
        onChange={(value) => set("outcome", value)}
      >
        {OUTCOMES.map((outcome) => (
          <ListBox.Item key={outcome} id={outcome} textValue={statusLabel(outcome, lang)}>
            {statusLabel(outcome, lang)}
            <ListBox.ItemIndicator />
          </ListBox.Item>
        ))}
      </FilterSelect>

      <FilterSelect
        className="w-full sm:w-52"
        label={isFa ? "عامل" : "Actor"}
        placeholder={isFa ? "همهٔ عامل‌ها" : "All actors"}
        value={filters.actorUserId}
        onChange={(value) => set("actorUserId", value)}
      >
        {members.map((member) => (
          <ListBox.Item key={member.user_id} id={member.user_id} textValue={memberName(member)}>
            {memberName(member)}
            <ListBox.ItemIndicator />
          </ListBox.Item>
        ))}
      </FilterSelect>

      <FilterSelect
        className="w-full sm:w-52"
        label={isFa ? "نوع هدف" : "Target type"}
        placeholder={isFa ? "همهٔ هدف‌ها" : "All targets"}
        value={filters.targetType}
        onChange={(value) => set("targetType", value)}
      >
        {auditTargetTypes().map((type) => (
          <ListBox.Item key={type} id={type} textValue={auditTargetLabel(type, lang)}>
            {auditTargetLabel(type, lang)}
            <ListBox.ItemIndicator />
          </ListBox.Item>
        ))}
      </FilterSelect>

      <DateRangePicker value={range} onChange={setRange} className="w-full sm:w-auto">
        <Label>{isFa ? "بازهٔ زمانی" : "Date range"}</Label>
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
            <DateRangePicker.Trigger aria-label={isFa ? "انتخاب بازهٔ زمانی" : "Choose a date range"}>
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

      {hasAuditFilters(filters) && (
        <Button variant="ghost" size="sm" onPress={() => onChange(NO_AUDIT_FILTERS)}>
          <FilterX size={15} aria-hidden="true" />
          {isFa ? "پاک کردن فیلترها" : "Clear filters"}
        </Button>
      )}
    </div>
  );
}
