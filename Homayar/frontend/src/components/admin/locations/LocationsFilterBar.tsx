import type { Key } from "react";
import { Input, Label, ListBox, Select, TextField } from "@heroui/react";

import { LOCATION_KINDS, type LocationsFilterState } from "@/components/admin/locations/location-model";
import { LOCATIONS_COPY } from "@/components/admin/locations/locations-copy";
import { useI18n } from "@/lib/i18n-context";
import type { LocationKind } from "@/types/locations";

/** The select id for "no filter": the explicit all-option each select offers. */
const ALL = "all";

function isKind(key: Key | null): key is LocationKind {
  return typeof key === "string" && (LOCATION_KINDS as readonly string[]).includes(key);
}

/**
 * The filter row of the locations page. Like the audit and processing bars it
 * holds no state of its own: every change is reported upwards. The city text
 * is the raw input; the page debounces it into the filters.
 */
export default function LocationsFilterBar({
  filters,
  cityInput,
  onFiltersChange,
  onCityInputChange,
}: {
  filters: LocationsFilterState;
  cityInput: string;
  onFiltersChange: (filters: LocationsFilterState) => void;
  onCityInputChange: (value: string) => void;
}) {
  const { lang } = useI18n();
  const copy = LOCATIONS_COPY[lang];

  return (
    <div className="flex flex-wrap items-end gap-3">
      <Select
        className="w-full sm:w-44"
        value={filters.kind || ALL}
        onChange={(key: Key | null) => onFiltersChange({ ...filters, kind: isKind(key) ? key : "" })}
        variant="secondary"
      >
        <Label>{copy.kind}</Label>
        <Select.Trigger>
          <Select.Value />
          <Select.Indicator />
        </Select.Trigger>
        <Select.Popover>
          <ListBox>
            <ListBox.Item key={ALL} id={ALL} textValue={copy.allKinds}>
              {copy.allKinds}
              <ListBox.ItemIndicator />
            </ListBox.Item>
            {LOCATION_KINDS.map((kind) => (
              <ListBox.Item key={kind} id={kind} textValue={copy.kinds[kind]}>
                {copy.kinds[kind]}
                <ListBox.ItemIndicator />
              </ListBox.Item>
            ))}
          </ListBox>
        </Select.Popover>
      </Select>

      <Select
        className="w-full sm:w-44"
        value={filters.status || ALL}
        onChange={(key: Key | null) =>
          onFiltersChange({ ...filters, status: key === "active" || key === "inactive" ? key : "" })
        }
        variant="secondary"
      >
        <Label>{copy.status}</Label>
        <Select.Trigger>
          <Select.Value />
          <Select.Indicator />
        </Select.Trigger>
        <Select.Popover>
          <ListBox>
            <ListBox.Item key={ALL} id={ALL} textValue={copy.allStatuses}>
              {copy.allStatuses}
              <ListBox.ItemIndicator />
            </ListBox.Item>
            <ListBox.Item key="active" id="active" textValue={copy.active}>
              {copy.active}
              <ListBox.ItemIndicator />
            </ListBox.Item>
            <ListBox.Item key="inactive" id="inactive" textValue={copy.inactive}>
              {copy.inactive}
              <ListBox.ItemIndicator />
            </ListBox.Item>
          </ListBox>
        </Select.Popover>
      </Select>

      <TextField className="w-full sm:w-52" value={cityInput} onChange={onCityInputChange} variant="secondary">
        <Label>{copy.city}</Label>
        <Input />
      </TextField>
    </div>
  );
}
