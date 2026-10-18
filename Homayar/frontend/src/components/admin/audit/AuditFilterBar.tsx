import { useState, type ComponentProps, type Key, type KeyboardEvent, type ReactNode } from "react";
import {
  Button,
  DateField,
  DateRangePicker,
  Description,
  Header,
  Input,
  Label,
  ListBox,
  RangeCalendar,
  Select,
  TextField,
} from "@heroui/react";
import { FilterX } from "lucide-react";
import type { CalendarDate } from "@internationalized/date";

import {
  auditEventGroupLabel,
  auditEventLabel,
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
import { useAuditEventTypes } from "@/lib/platform-api";
import { statusLabel } from "@/lib/status-labels";
import type { AuditEventTypeEntry, WorkspaceMember } from "@/types/platform";

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
  isDisabled = false,
}: {
  label: string;
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
  className?: string;
  isDisabled?: boolean;
}) {
  return (
    <Select
      className={className}
      placeholder={placeholder}
      value={value || null}
      onChange={(key: Key | null) => onChange(typeof key === "string" ? key : "")}
      onClear={() => onChange("")}
      variant="secondary"
      isDisabled={isDisabled}
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

/** The catalog's groups, in order of first appearance, each with its types in catalog order. */
function catalogGroups(items: AuditEventTypeEntry[]): Array<{ group: string; types: string[] }> {
  const groups = new Map<string, string[]>();
  for (const entry of items) {
    const types = groups.get(entry.group);
    if (types) types.push(entry.event_type);
    else groups.set(entry.group, [entry.event_type]);
  }
  return [...groups].map(([group, types]) => ({ group, types }));
}

/** One event-type option: its label, or its raw name when no label exists. */
function EventTypeOption({ type, lang }: { type: string; lang: "fa" | "en" }) {
  const label = auditEventLabel(type, lang);
  return (
    <ListBox.Item key={type} id={type} textValue={label ?? type}>
      {label ?? <bdi dir="ltr">{type}</bdi>}
      <ListBox.ItemIndicator />
    </ListBox.Item>
  );
}

/**
 * The free-text stand-in for the event-type select, used when the catalog
 * could not be loaded. Typing only changes the draft held by the parent;
 * Enter or blur commits the trimmed text to the URL, and an empty text
 * clears the filter.
 */
function EventTypeFallbackField({
  value,
  onChange,
  onCommit,
  lang,
}: {
  value: string;
  onChange: (value: string) => void;
  onCommit: () => void;
  lang: "fa" | "en";
}) {
  const isFa = lang === "fa";

  return (
    <TextField
      className="w-full sm:w-64"
      value={value}
      onChange={onChange}
      onBlur={onCommit}
      variant="secondary"
    >
      <Label>{isFa ? "نوع رویداد" : "Event type"}</Label>
      <Input
        dir="ltr"
        autoComplete="off"
        spellCheck={false}
        onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
          if (event.key !== "Enter") return;
          event.preventDefault();
          onCommit();
        }}
      />
      <Description>
        {isFa
          ? "فهرست انواع رویداد دریافت نشد. نام دقیق نوع رویداد را بنویسید، مثلاً member.role_changed."
          : "Event types could not be loaded. Type the exact event type, for example member.role_changed."}
      </Description>
    </TextField>
  );
}

/**
 * The filter row of the audit log. Every committed change is reported
 * upwards and ends up in the URL, so the filtered view can be shared and
 * reloaded. The one local state is the fallback field's uncommitted draft.
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
  const catalog = useAuditEventTypes();
  // The fallback field's uncommitted text; null means "follow the URL".
  const [eventTypeDraft, setEventTypeDraft] = useState<string | null>(null);
  const start = parseAuditDay(filters.from);
  const end = parseAuditDay(filters.to);
  // React Aria wants both ends or nothing; one lone day is not a range yet.
  const range: DateRange | null = start && end ? { start, end } : null;

  // The catalog decides the offered types. A URL value outside it (an old
  // shared link) stays chosen through one extra option, so the link keeps
  // meaning what it meant when it was shared.
  const catalogItems = catalog.data?.items ?? [];
  const offered = new Set(catalogItems.map((entry) => entry.event_type));
  const unlisted = filters.eventType && !offered.has(filters.eventType) ? filters.eventType : "";

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
      {catalog.isError ? (
        <EventTypeFallbackField
          value={eventTypeDraft ?? filters.eventType}
          onChange={setEventTypeDraft}
          onCommit={() => {
            const trimmed = (eventTypeDraft ?? filters.eventType).trim();
            setEventTypeDraft(null);
            // The same text again would only push a duplicate history entry.
            if (trimmed !== filters.eventType) set("eventType", trimmed);
          }}
          lang={lang}
        />
      ) : (
        <FilterSelect
          className="w-full sm:w-64"
          label={isFa ? "نوع رویداد" : "Event type"}
          // While the catalog loads, a URL value has no option to show yet,
          // so the trigger carries it as its text.
          placeholder={
            catalog.isPending && filters.eventType
              ? (auditEventLabel(filters.eventType, lang) ?? filters.eventType)
              : (isFa ? "همهٔ رویدادها" : "All events")
          }
          value={filters.eventType}
          onChange={(value) => set("eventType", value)}
          isDisabled={catalog.isPending}
        >
          {catalogGroups(catalogItems).map((group) => (
            <ListBox.Section key={group.group}>
              <Header className="px-2 py-1 text-xs text-muted">
                {auditEventGroupLabel(group.group, lang)}
              </Header>
              {group.types.map((type) => (
                <EventTypeOption key={type} type={type} lang={lang} />
              ))}
            </ListBox.Section>
          ))}
          {!catalog.isPending && unlisted ? (
            <ListBox.Section key="unlisted">
              <EventTypeOption type={unlisted} lang={lang} />
            </ListBox.Section>
          ) : null}
        </FilterSelect>
      )}

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
