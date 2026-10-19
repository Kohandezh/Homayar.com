import type { Key } from "react";
import { Label, ListBox, Select } from "@heroui/react";

import {
  isVoicemailCategory,
  isVoicemailPriority,
  VOICEMAIL_CATEGORIES,
  VOICEMAIL_PRIORITIES,
  type VoicemailFilters,
} from "@/lib/voicemail-filters";
import { VOICEMAIL_COPY, voicemailCategoryLabel, voicemailPriorityLabel } from "@/components/voicemail/voicemail-copy";
import { useI18n } from "@/lib/i18n-context";

/** The id of the option that means "no filter"; shown as the select's "All". */
const ALL = "all";

/**
 * The filter row of the voicemail inbox. It never holds state of its own:
 * every change is reported upwards and ends up in the URL, so a filtered view
 * can be shared and reloaded.
 */
export default function VoicemailFilterBar({
  filters,
  onChange,
}: {
  filters: VoicemailFilters;
  onChange: (filters: VoicemailFilters) => void;
}) {
  const { lang } = useI18n();
  const copy = VOICEMAIL_COPY[lang];

  return (
    <div className="flex flex-wrap items-end gap-3">
      <Select
        className="w-full sm:w-64"
        value={filters.priority ?? ALL}
        onChange={(key: Key | null) =>
          onChange({ ...filters, priority: isVoicemailPriority(key) ? key : null })
        }
        variant="secondary"
      >
        <Label>{copy.filterPriority}</Label>
        <Select.Trigger>
          <Select.Value />
          <Select.Indicator />
        </Select.Trigger>
        <Select.Popover>
          <ListBox>
            <ListBox.Item key={ALL} id={ALL} textValue={copy.all}>
              {copy.all}
              <ListBox.ItemIndicator />
            </ListBox.Item>
            {VOICEMAIL_PRIORITIES.map((priority) => (
              <ListBox.Item
                key={priority}
                id={priority}
                textValue={voicemailPriorityLabel(priority, lang)}
              >
                {voicemailPriorityLabel(priority, lang)}
                <ListBox.ItemIndicator />
              </ListBox.Item>
            ))}
          </ListBox>
        </Select.Popover>
      </Select>

      <Select
        className="w-full sm:w-64"
        value={filters.category ?? ALL}
        onChange={(key: Key | null) =>
          onChange({ ...filters, category: isVoicemailCategory(key) ? key : null })
        }
        variant="secondary"
      >
        <Label>{copy.filterCategory}</Label>
        <Select.Trigger>
          <Select.Value />
          <Select.Indicator />
        </Select.Trigger>
        <Select.Popover>
          <ListBox>
            <ListBox.Item key={ALL} id={ALL} textValue={copy.all}>
              {copy.all}
              <ListBox.ItemIndicator />
            </ListBox.Item>
            {VOICEMAIL_CATEGORIES.map((category) => (
              <ListBox.Item
                key={category}
                id={category}
                textValue={voicemailCategoryLabel(category, lang)}
              >
                {voicemailCategoryLabel(category, lang)}
                <ListBox.ItemIndicator />
              </ListBox.Item>
            ))}
          </ListBox>
        </Select.Popover>
      </Select>
    </div>
  );
}
