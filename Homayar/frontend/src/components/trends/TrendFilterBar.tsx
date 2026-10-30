import type { ComponentProps, Key } from "react";
import {
  DateField,
  DateRangePicker,
  Label,
  ListBox,
  RangeCalendar,
  Select,
  ToggleButton,
  ToggleButtonGroup,
} from "@heroui/react";
import type { CalendarDate } from "@internationalized/date";

import { TRENDS_COPY } from "@/components/trends/trends-copy";
import { parseAuditDay } from "@/lib/audit-filters";
import { useI18n } from "@/lib/i18n-context";
import {
  activeTrendPreset,
  TREND_PRESETS,
  trendPresetFilters,
  type TrendFilterGrain,
  type TrendFilters,
  type TrendPreset,
} from "@/lib/trend-filters";
import type { Team } from "@/types/platform";

type DateSegment = ComponentProps<typeof DateField.Segment>["segment"];

/** The Select key of "All teams"; never sent, it only means "no team_id". */
const ALL_TEAMS = "__all__";
const GRAINS: readonly TrendFilterGrain[] = ["week", "month"];

/**
 * The single choice of a toggle group. The group runs in "multiple" mode so
 * each choice is a real button with `aria-pressed`; this keeps one choice
 * and refuses to empty it.
 */
function chosen<T extends string>(keys: Set<Key>, current: T | null): T | null {
  const next = [...keys].find((key) => key !== current);
  return typeof next === "string" ? (next as T) : null;
}

/**
 * The filter row of the trends page. It holds no state of its own: every
 * change is reported upwards and ends up in the URL.
 */
export default function TrendFilterBar({
  filters,
  now,
  teams,
  onChange,
}: {
  filters: TrendFilters;
  /** The moment the presets count from. */
  now: Date;
  teams: Team[];
  onChange: (filters: TrendFilters) => void;
}) {
  const { lang } = useI18n();
  const copy = TRENDS_COPY[lang];
  const preset = activeTrendPreset(filters, now);
  const start = parseAuditDay(filters.from);
  const end = parseAuditDay(filters.to);
  const range = start && end ? { start, end } : null;

  function setRange(next: { start: CalendarDate; end: CalendarDate } | null) {
    if (!next) return;
    onChange({
      ...filters,
      // `toString()` is always the Gregorian day, whatever calendar is shown.
      from: next.start.toString().slice(0, 10),
      to: next.end.toString().slice(0, 10),
    });
  }

  return (
    <div className="flex flex-wrap items-end gap-3">
      <ToggleButtonGroup
        aria-label={copy.presetsLabel}
        selectionMode="multiple"
        selectedKeys={preset ? [preset] : []}
        onSelectionChange={(keys) => {
          const next = chosen<TrendPreset>(keys, preset);
          if (next) onChange({ ...filters, ...trendPresetFilters(next, now) });
        }}
        size="sm"
      >
        {TREND_PRESETS.map((item) => (
          <ToggleButton key={item} id={item}>
            {copy.presets[item]}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>

      <DateRangePicker value={range} onChange={setRange} className="w-full sm:w-auto">
        <Label>{copy.dateRange}</Label>
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
            <DateRangePicker.Trigger aria-label={copy.chooseDateRange}>
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

      <Select
        className="w-full sm:w-56"
        value={filters.teamId || ALL_TEAMS}
        onChange={(key: Key | null) =>
          onChange({ ...filters, teamId: typeof key === "string" && key !== ALL_TEAMS ? key : "" })
        }
        variant="secondary"
      >
        <Label>{copy.team}</Label>
        <Select.Trigger>
          <Select.Value />
          <Select.Indicator />
        </Select.Trigger>
        <Select.Popover>
          <ListBox>
            <ListBox.Item id={ALL_TEAMS} textValue={copy.allTeams}>
              {copy.allTeams}
              <ListBox.ItemIndicator />
            </ListBox.Item>
            {teams.map((team) => (
              <ListBox.Item key={team.id} id={team.id} textValue={team.name}>
                <bdi>{team.name}</bdi>
                <ListBox.ItemIndicator />
              </ListBox.Item>
            ))}
          </ListBox>
        </Select.Popover>
      </Select>

      <ToggleButtonGroup
        aria-label={copy.grainLabel}
        selectionMode="multiple"
        selectedKeys={[filters.grain]}
        onSelectionChange={(keys) => {
          const next = chosen<TrendFilterGrain>(keys, filters.grain);
          if (next) onChange({ ...filters, grain: next });
        }}
        size="sm"
      >
        {GRAINS.map((grain) => (
          <ToggleButton key={grain} id={grain}>
            {copy.grains[grain]}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
    </div>
  );
}
