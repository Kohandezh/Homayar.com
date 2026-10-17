import type { Key } from "react";
import { Button, Label, ListBox, Select } from "@heroui/react";
import { FilterX } from "lucide-react";

import {
  hasJobFilters,
  isJobState,
  JOB_STAGES,
  JOB_STATE_FILTERS,
  NO_JOB_FILTERS,
  type JobFilters,
} from "@/lib/job-filters";
import { jobStageLabel } from "@/lib/job-labels";
import { useI18n } from "@/lib/i18n-context";
import { statusLabel } from "@/lib/status-labels";

/**
 * The filter row of the processing page. It never holds state of its own:
 * every change is reported upwards and ends up in the URL, so the filtered
 * view can be shared and reloaded.
 */
export default function JobFilterBar({
  filters,
  onChange,
}: {
  filters: JobFilters;
  onChange: (filters: JobFilters) => void;
}) {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  // A stage from a shared URL can be any free string. While it filters the
  // list it is offered as an extra option, so the select never claims "All
  // stages" over a filtered list.
  const unknownStage =
    filters.stage && !JOB_STAGES.includes(filters.stage) ? filters.stage : null;

  return (
    <div className="flex flex-wrap items-end gap-3">
      {/* Several states at once: "what failed" is `failed` plus `dead_letter`. */}
      <Select
        selectionMode="multiple"
        className="w-full sm:w-72"
        placeholder={isFa ? "همهٔ وضعیت‌ها" : "All states"}
        value={filters.states}
        onChange={(keys: Key[]) => onChange({ ...filters, states: keys.filter(isJobState) })}
        onClear={() => onChange({ ...filters, states: [] })}
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
            {JOB_STATE_FILTERS.map((state) => (
              <ListBox.Item key={state} id={state} textValue={statusLabel(state, lang)}>
                {statusLabel(state, lang)}
                <ListBox.ItemIndicator />
              </ListBox.Item>
            ))}
          </ListBox>
        </Select.Popover>
      </Select>

      <Select
        className="w-full sm:w-64"
        placeholder={isFa ? "همهٔ مرحله‌ها" : "All stages"}
        value={filters.stage || null}
        onChange={(key: Key | null) =>
          onChange({ ...filters, stage: typeof key === "string" ? key : "" })
        }
        onClear={() => onChange({ ...filters, stage: "" })}
        variant="secondary"
      >
        <Label>{isFa ? "مرحله" : "Stage"}</Label>
        <Select.Trigger>
          <Select.Value />
          <Select.ClearButton />
          <Select.Indicator />
        </Select.Trigger>
        <Select.Popover>
          <ListBox>
            {unknownStage !== null && (
              <ListBox.Item key={unknownStage} id={unknownStage} textValue={unknownStage}>
                <bdi dir="ltr">{unknownStage}</bdi>
                <ListBox.ItemIndicator />
              </ListBox.Item>
            )}
            {JOB_STAGES.map((stage) => (
              <ListBox.Item key={stage} id={stage} textValue={jobStageLabel(stage, lang)}>
                {jobStageLabel(stage, lang)}
                <ListBox.ItemIndicator />
              </ListBox.Item>
            ))}
          </ListBox>
        </Select.Popover>
      </Select>

      {hasJobFilters(filters) && (
        <Button variant="ghost" size="sm" onPress={() => onChange(NO_JOB_FILTERS)}>
          <FilterX size={15} aria-hidden="true" />
          {isFa ? "پاک کردن فیلترها" : "Clear filters"}
        </Button>
      )}
    </div>
  );
}
