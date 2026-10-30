import { useId, useState } from "react";
import { Chip, Label, ListBox, Select } from "@heroui/react";

import {
  CONVERSATION_TYPES,
  DIRECTIONS,
  criterionApplicability,
  weightTotal,
  type Applicability,
} from "@/components/admin/scorecards/scorecard-model";
import { SCORECARDS_COPY } from "@/components/admin/scorecards/scorecards-copy";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import type { ConversationDirection, ConversationType, ScorecardApplicability } from "@/types/scorecards";

export interface PreviewCriterion {
  /** A stable React key. */
  id: string;
  criterion_id: string;
  label: string;
  weight: number;
  applies_to: ScorecardApplicability | null;
}

/** "unknown" in the direction select stands for a direction that is not known (null). */
type DirectionChoice = ConversationDirection | "unknown";

const RESULT_COLORS: Record<Applicability, "success" | "default" | "warning"> = {
  applies: "success",
  not_applicable: "default",
  unknown: "warning",
};

/**
 * Which criteria count for one sample conversation, by the same rule the
 * scoring uses (criterionApplicability). Only "Applies" adds to the weight.
 */
export default function ScorecardPreview({ criteria }: { criteria: PreviewCriterion[] }) {
  const { lang } = useI18n();
  const copy = SCORECARDS_COPY[lang];
  const headingId = useId();
  const [type, setType] = useState<ConversationType>("call");
  const [direction, setDirection] = useState<DirectionChoice>("inbound");
  const conversation = { conversation_type: type, direction: direction === "unknown" ? null : direction };
  const results = criteria.map((criterion) => ({
    criterion,
    result: criterionApplicability(criterion, conversation),
  }));
  const total = weightTotal(criteria);
  const applicable = weightTotal(results.filter(({ result }) => result === "applies").map(({ criterion }) => criterion));
  const directionChoices: DirectionChoice[] = [...DIRECTIONS, "unknown"];

  return (
    <section className="platform-surface grid gap-3 p-4" aria-labelledby={headingId}>
      <div>
        <h3 id={headingId} className="m-0 text-sm font-semibold text-text-primary">
          {copy.preview}
        </h3>
        <p className="mt-1 mb-0 text-xs text-muted">{copy.previewIntro}</p>
      </div>
      <div className="flex flex-wrap gap-3">
        <Select
          variant="secondary"
          className="min-w-40"
          value={type}
          onChange={(value) => {
            if (typeof value === "string" && (CONVERSATION_TYPES as readonly string[]).includes(value)) {
              setType(value as ConversationType);
            }
          }}
        >
          <Label>{copy.callType}</Label>
          <Select.Trigger>
            <Select.Value />
            <Select.Indicator />
          </Select.Trigger>
          <Select.Popover>
            <ListBox>
              {CONVERSATION_TYPES.map((choice) => (
                <ListBox.Item key={choice} id={choice} textValue={copy.types[choice]}>
                  {copy.types[choice]}
                  <ListBox.ItemIndicator />
                </ListBox.Item>
              ))}
            </ListBox>
          </Select.Popover>
        </Select>
        <Select
          variant="secondary"
          className="min-w-40"
          value={direction}
          onChange={(value) => {
            if (typeof value === "string" && (directionChoices as string[]).includes(value)) {
              setDirection(value as DirectionChoice);
            }
          }}
        >
          <Label>{copy.direction}</Label>
          <Select.Trigger>
            <Select.Value />
            <Select.Indicator />
          </Select.Trigger>
          <Select.Popover>
            <ListBox>
              {directionChoices.map((choice) => {
                const text = choice === "unknown" ? copy.unknownDirection : copy.directions[choice];
                return (
                  <ListBox.Item key={choice} id={choice} textValue={text}>
                    {text}
                    <ListBox.ItemIndicator />
                  </ListBox.Item>
                );
              })}
            </ListBox>
          </Select.Popover>
        </Select>
      </div>
      <ul className="m-0 grid gap-1.5 p-0">
        {results.map(({ criterion, result }) => (
          <li key={criterion.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span className="min-w-0">
              {criterion.label ? (
                <bdi>{criterion.label}</bdi>
              ) : (
                <bdi dir="ltr" className="font-mono text-xs">
                  {criterion.criterion_id || "—"}
                </bdi>
              )}
              <span className="text-muted">
                {" · "}
                {copy.weight} {formatNumber(criterion.weight, lang)}
              </span>
            </span>
            <Chip size="sm" variant="soft" color={RESULT_COLORS[result]}>
              <Chip.Label>{copy.results[result]}</Chip.Label>
            </Chip>
          </li>
        ))}
      </ul>
      <p className="m-0 text-sm font-medium">
        {copy.weightCounts(formatNumber(applicable, lang), formatNumber(total, lang))}
      </p>
    </section>
  );
}
