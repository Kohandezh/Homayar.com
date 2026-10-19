import type { ComponentProps, Key } from "react";
import {
  Button,
  DateField,
  DateRangePicker,
  Label,
  ListBox,
  RangeCalendar,
  Select,
} from "@heroui/react";
import { FilterX } from "lucide-react";
import type { CalendarDate } from "@internationalized/date";

import {
  hasIngestionFilters,
  INGESTION_SOURCE_TYPES,
  INGESTION_STATES,
  NO_INGESTION_FILTERS,
  parseIngestionDay,
  type IngestionFilters,
} from "@/lib/ingestion-filters";
import { ingestionSourceLabel, ingestionStatusLabel } from "@/lib/ingestion-labels";
import { useI18n } from "@/lib/i18n-context";

type DateSegment = ComponentProps<typeof DateField.Segment>["segment"];

interface DateRange {
  start: CalendarDate;
  end: CalendarDate;
}

/**
 * The filter row of the ingestions page. It never holds state of its own:
 * every change is reported upwards and ends up in the URL, so the filtered
 * view can be shared and reloaded.
 */
export default function IngestionFilterBar({
  filters,
  onChange,
}: {
  filters: IngestionFilters;
  onChange: (filters: IngestionFilters) => void;
}) {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  // React Aria wants both ends or nothing; one lone day is not a range yet.
  const start = parseIngestionDay(filters.from);
  const end = parseIngestionDay(filters.to);
  const range: DateRange | null = start && end ? { start, end } : null;

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
      <Select
        className="w-full sm:w-64"
        placeholder={isFa ? "همهٔ منبع‌ها" : "All sources"}
        value={filters.source || null}
        onChange={(key: Key | null) =>
          onChange({ ...filters, source: typeof key === "string" ? key : "" })
        }
        onClear={() => onChange({ ...filters, source: "" })}
        variant="secondary"
      >
        <Label>{isFa ? "منبع" : "Source"}</Label>
        <Select.Trigger>
          <Select.Value />
          <Select.ClearButton />
          <Select.Indicator />
        </Select.Trigger>
        <Select.Popover>
          <ListBox>
            {INGESTION_SOURCE_TYPES.map((source) => (
              <ListBox.Item key={source} id={source} textValue={ingestionSourceLabel(source, lang)}>
                {ingestionSourceLabel(source, lang)}
                <ListBox.ItemIndicator />
              </ListBox.Item>
            ))}
          </ListBox>
        </Select.Popover>
      </Select>

      <Select
        className="w-full sm:w-40"
        placeholder={isFa ? "همهٔ وضعیت‌ها" : "All states"}
        value={filters.state || null}
        onChange={(key: Key | null) =>
          onChange({ ...filters, state: typeof key === "string" ? key : "" })
        }
        onClear={() => onChange({ ...filters, state: "" })}
        variant="secondary"
      >
        <Label>{isFa ? "وضعیت" : "State"}</Label>
        <Select.Trigger>
          <Select.Value />
          <Select.ClearButton />
          <Select.Indicator />
        </Select.Trigger>
        <Select.Popover>
          <ListBox>
            {INGESTION_STATES.map((state) => (
              <ListBox.Item key={state} id={state} textValue={ingestionStatusLabel(state, lang)}>
                {ingestionStatusLabel(state, lang)}
                <ListBox.ItemIndicator />
              </ListBox.Item>
            ))}
          </ListBox>
        </Select.Popover>
      </Select>

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

      {hasIngestionFilters(filters) && (
        <Button variant="ghost" size="sm" onPress={() => onChange(NO_INGESTION_FILTERS)}>
          <FilterX size={15} aria-hidden="true" />
          {isFa ? "پاک کردن فیلترها" : "Clear filters"}
        </Button>
      )}
    </div>
  );
}
