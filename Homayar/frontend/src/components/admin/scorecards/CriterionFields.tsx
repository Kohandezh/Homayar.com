import {
  Button,
  Checkbox,
  CheckboxGroup,
  Description,
  FieldError,
  Fieldset,
  Input,
  Label,
  ListBox,
  Select,
  TextArea,
  TextField,
} from "@heroui/react";
import { Trash2 } from "lucide-react";

import {
  CHECK_KINDS,
  CONVERSATION_TYPES,
  DIRECTIONS,
  SCOPE_KINDS,
  SPEAKER_ROLES,
  type CriterionDraft,
  type CriterionIssues,
} from "@/components/admin/scorecards/scorecard-model";
import { SCORECARDS_COPY } from "@/components/admin/scorecards/scorecards-copy";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import type {
  ConversationDirection,
  ConversationType,
  ScorecardCheckKind,
  ScorecardScopeKind,
  SpeakerRole,
} from "@/types/scorecards";

/** A small Select over fixed choices; the value is always one of `choices`. */
function ChoiceSelect<T extends string>({
  label,
  value,
  choices,
  text,
  isDisabled,
  onChange,
}: {
  label: string;
  value: T;
  choices: readonly T[];
  text: (choice: T) => string;
  isDisabled: boolean;
  onChange: (value: T) => void;
}) {
  return (
    <Select
      variant="secondary"
      fullWidth
      value={value}
      isDisabled={isDisabled}
      onChange={(next) => {
        if (typeof next === "string" && (choices as readonly string[]).includes(next)) onChange(next as T);
      }}
    >
      <Label>{label}</Label>
      <Select.Trigger>
        <Select.Value />
        <Select.Indicator />
      </Select.Trigger>
      <Select.Popover>
        <ListBox>
          {choices.map((choice) => (
            <ListBox.Item key={choice} id={choice} textValue={text(choice)}>
              {text(choice)}
              <ListBox.ItemIndicator />
            </ListBox.Item>
          ))}
        </ListBox>
      </Select.Popover>
    </Select>
  );
}

function CheckboxItem({ value, label }: { value: string; label: string }) {
  return (
    <Checkbox value={value}>
      <Checkbox.Content>
        <Checkbox.Control>
          <Checkbox.Indicator />
        </Checkbox.Control>
        {label}
      </Checkbox.Content>
    </Checkbox>
  );
}

/**
 * One criterion of the editor, a fieldset named "Criterion <n>". A wrong
 * weight or turns value shows its error at once, so a disabled Publish
 * always has a visible cause.
 */
export default function CriterionFields({
  criterion,
  index,
  issues,
  canRemove,
  isDisabled,
  onChange,
  onRemove,
}: {
  criterion: CriterionDraft;
  index: number;
  issues: CriterionIssues;
  canRemove: boolean;
  isDisabled: boolean;
  onChange: (patch: Partial<CriterionDraft>) => void;
  onRemove: () => void;
}) {
  const { lang } = useI18n();
  const copy = SCORECARDS_COPY[lang];
  const weightInvalid = issues.weight !== null;
  const turnsInvalid = issues.turns !== null;

  return (
    <Fieldset className="platform-surface min-w-0 p-4">
      <Fieldset.Legend>{copy.criterion(formatNumber(index + 1, lang))}</Fieldset.Legend>
      <Fieldset.Group className="grid gap-4">
        <div className="grid gap-4 sm:grid-cols-[1fr_2fr_8rem]">
          <TextField
            variant="secondary"
            value={criterion.criterion_id}
            onChange={(value) => onChange({ criterion_id: value })}
            isDisabled={isDisabled}
          >
            <Label>{copy.criterionId}</Label>
            <Input dir="ltr" autoComplete="off" spellCheck={false} />
          </TextField>
          <TextField
            variant="secondary"
            value={criterion.label}
            onChange={(value) => onChange({ label: value })}
            isDisabled={isDisabled}
          >
            <Label>{copy.label}</Label>
            <Input dir="auto" autoComplete="off" maxLength={200} />
          </TextField>
          <TextField
            variant="secondary"
            value={criterion.weight}
            onChange={(value) => onChange({ weight: value })}
            isInvalid={weightInvalid}
            isDisabled={isDisabled}
          >
            <Label>{copy.weight}</Label>
            <Input dir="ltr" inputMode="numeric" autoComplete="off" />
            <FieldError>{copy.weightInvalid}</FieldError>
          </TextField>
        </div>

        <div className="flex flex-wrap gap-4">
          <Checkbox
            isSelected={criterion.auto_fail}
            onChange={(selected) => onChange({ auto_fail: selected })}
            isDisabled={isDisabled}
          >
            <Checkbox.Content>
              <Checkbox.Control>
                <Checkbox.Indicator />
              </Checkbox.Control>
              {copy.autoFail}
            </Checkbox.Content>
          </Checkbox>
          <Checkbox
            isSelected={criterion.na_allowed}
            onChange={(selected) => onChange({ na_allowed: selected })}
            isDisabled={isDisabled}
          >
            <Checkbox.Content>
              <Checkbox.Control>
                <Checkbox.Indicator />
              </Checkbox.Control>
              {copy.naAllowed}
            </Checkbox.Content>
          </Checkbox>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <CheckboxGroup
            value={criterion.conversation_types}
            onChange={(values) =>
              onChange({
                conversation_types: CONVERSATION_TYPES.filter((type) => values.includes(type)) as ConversationType[],
              })
            }
            isDisabled={isDisabled}
          >
            <Label>{copy.conversationTypes}</Label>
            <Description>{copy.groupHint}</Description>
            <div className="flex flex-wrap gap-3">
              {CONVERSATION_TYPES.map((type) => (
                <CheckboxItem key={type} value={type} label={copy.types[type]} />
              ))}
            </div>
          </CheckboxGroup>
          <CheckboxGroup
            value={criterion.directions}
            onChange={(values) =>
              onChange({
                directions: DIRECTIONS.filter((direction) => values.includes(direction)) as ConversationDirection[],
              })
            }
            isDisabled={isDisabled}
          >
            <Label>{copy.directionsLabel}</Label>
            <Description>{copy.groupHint}</Description>
            <div className="flex flex-wrap gap-3">
              {DIRECTIONS.map((direction) => (
                <CheckboxItem key={direction} value={direction} label={copy.directions[direction]} />
              ))}
            </div>
          </CheckboxGroup>
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <ChoiceSelect<ScorecardCheckKind>
            label={copy.checkType}
            value={criterion.kind}
            choices={CHECK_KINDS}
            text={(kind) => copy.kinds[kind]}
            isDisabled={isDisabled}
            onChange={(kind) => onChange({ kind })}
          />
          <ChoiceSelect<SpeakerRole>
            label={copy.speaker}
            value={criterion.speaker_role}
            choices={SPEAKER_ROLES}
            text={(role) => copy.speakers[role]}
            isDisabled={isDisabled}
            onChange={(speaker_role) => onChange({ speaker_role })}
          />
          <ChoiceSelect<ScorecardScopeKind>
            label={copy.scope}
            value={criterion.scope_kind}
            choices={SCOPE_KINDS}
            text={(kind) => copy.scopes[kind]}
            isDisabled={isDisabled}
            onChange={(scope_kind) => onChange({ scope_kind })}
          />
        </div>

        {criterion.scope_kind !== "whole_call" && (
          <TextField
            variant="secondary"
            className="max-w-40"
            value={criterion.max_turns}
            onChange={(value) => onChange({ max_turns: value })}
            isInvalid={turnsInvalid}
            isDisabled={isDisabled}
          >
            <Label>{copy.turns}</Label>
            <Input dir="ltr" inputMode="numeric" autoComplete="off" />
            <FieldError>{copy.turnsInvalid}</FieldError>
          </TextField>
        )}

        {criterion.kind === "contextual" ? (
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              variant="secondary"
              value={criterion.question}
              onChange={(value) => onChange({ question: value })}
              isDisabled={isDisabled}
            >
              <Label>{copy.question}</Label>
              <TextArea dir="auto" rows={3} autoComplete="off" />
            </TextField>
            <TextField
              variant="secondary"
              value={criterion.trigger_phrases}
              onChange={(value) => onChange({ trigger_phrases: value })}
              isDisabled={isDisabled}
            >
              <Label>{copy.triggerPhrases}</Label>
              <TextArea dir="auto" rows={3} autoComplete="off" />
              <Description>{copy.triggerHint}</Description>
            </TextField>
          </div>
        ) : (
          <TextField
            variant="secondary"
            value={criterion.phrases}
            onChange={(value) => onChange({ phrases: value })}
            isDisabled={isDisabled}
          >
            <Label>{copy.phrases}</Label>
            <TextArea dir="auto" rows={3} autoComplete="off" />
            <Description>{copy.phrasesHint}</Description>
          </TextField>
        )}
      </Fieldset.Group>
      <Fieldset.Actions className="mt-3 flex justify-end">
        <Button variant="ghost" size="sm" isDisabled={!canRemove || isDisabled} onPress={onRemove}>
          <Trash2 aria-hidden="true" size={15} />
          {copy.removeCriterion}
        </Button>
      </Fieldset.Actions>
    </Fieldset>
  );
}
