import { useId } from "react";
import { Chip } from "@heroui/react";

import ScorecardPreview from "@/components/admin/scorecards/ScorecardPreview";
import { CONVERSATION_TYPES, DIRECTIONS } from "@/components/admin/scorecards/scorecard-model";
import { SCORECARDS_COPY, type ScorecardsCopy } from "@/components/admin/scorecards/scorecards-copy";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import type { ScorecardApplicability, ScorecardCheck, ScorecardDefinition } from "@/types/scorecards";

function applicabilityText(copy: ScorecardsCopy, rule: ScorecardApplicability | null): string {
  if (rule === null) return copy.appliesToEverything;
  const types = rule.conversation_types
    ? CONVERSATION_TYPES.filter((type) => rule.conversation_types?.includes(type)).map((type) => copy.types[type]).join(", ")
    : copy.allTypes;
  const directions = rule.directions
    ? DIRECTIONS.filter((direction) => rule.directions?.includes(direction)).map((direction) => copy.directions[direction]).join(", ")
    : copy.allDirections;
  return `${copy.appliesTo}: ${types} · ${directions}`;
}

function scopeText(copy: ScorecardsCopy, check: ScorecardCheck, lang: string): string {
  const scope = check.scope ?? { kind: "whole_call" as const };
  return scope.kind === "whole_call"
    ? copy.scopes.whole_call
    : copy.scopeWithTurns(copy.scopes[scope.kind], formatNumber(scope.max_turns, lang));
}

/** Phrases a person typed: each in its own isolate, so mixed scripts keep their order. */
function PhraseList({ phrases }: { phrases: readonly string[] }) {
  const { lang } = useI18n();
  const separator = lang === "fa" ? "، " : ", ";
  return (
    <>
      {phrases.map((phrase, index) => (
        <span key={`${index}:${phrase}`}>
          {index > 0 && separator}
          <bdi>{phrase}</bdi>
        </span>
      ))}
    </>
  );
}

/** One stored version, read only: threshold, criteria, their checks, and the preview. */
export default function ScorecardDefinitionView({ definition }: { definition: ScorecardDefinition }) {
  const { lang } = useI18n();
  const copy = SCORECARDS_COPY[lang];
  const headingId = useId();

  return (
    <div className="grid gap-4">
      <section className="platform-surface grid gap-3 p-4 sm:p-5" aria-labelledby={headingId}>
        <div>
          <h2 id={headingId} className="m-0 text-base font-semibold text-text-primary">
            <bdi>{definition.name}</bdi>
          </h2>
          {definition.description && (
            <p className="mt-1 mb-0 text-sm text-muted">
              <bdi>{definition.description}</bdi>
            </p>
          )}
          <p className="mt-2 mb-0 text-sm">
            {copy.passThreshold}: {copy.percent(formatNumber(definition.pass_threshold, lang))}
          </p>
        </div>

        <h3 className="m-0 text-sm font-semibold text-text-primary">{copy.criteria}</h3>
        <ol className="m-0 grid gap-3 ps-5">
          {definition.criteria.map((criterion) => {
            const { check } = criterion;
            return (
              <li key={criterion.criterion_id} className="grid gap-1 text-sm">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">
                    <bdi>{criterion.label}</bdi>
                  </span>
                  <bdi dir="ltr" className="font-mono text-xs text-muted">
                    {criterion.criterion_id}
                  </bdi>
                  <span>
                    {copy.weight}: {formatNumber(criterion.weight, lang)}
                  </span>
                  {criterion.auto_fail && (
                    <Chip size="sm" variant="soft" color="danger">
                      <Chip.Label>{copy.autoFail}</Chip.Label>
                    </Chip>
                  )}
                  {criterion.na_allowed && (
                    <Chip size="sm" variant="soft">
                      <Chip.Label>{copy.naAllowedNote}</Chip.Label>
                    </Chip>
                  )}
                </span>
                <span className="text-muted">{applicabilityText(copy, criterion.applies_to)}</span>
                <span>
                  {copy.kinds[check.kind]}
                  {" · "}
                  {copy.speaker}: {copy.speakers[check.speaker_role ?? "agent"]}
                  {" · "}
                  {copy.scope}: {scopeText(copy, check, lang)}
                </span>
                {check.kind === "contextual" ? (
                  <>
                    <span>
                      {copy.question}: <bdi>{check.question}</bdi>
                    </span>
                    {(check.trigger_phrases ?? []).length > 0 && (
                      <span>
                        {copy.triggerPhrases}: <PhraseList phrases={check.trigger_phrases ?? []} />
                      </span>
                    )}
                  </>
                ) : (
                  <span>
                    {copy.phrases}: <PhraseList phrases={check.phrases} />
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      </section>

      <ScorecardPreview
        criteria={definition.criteria.map((criterion) => ({
          id: criterion.criterion_id,
          criterion_id: criterion.criterion_id,
          label: criterion.label,
          weight: criterion.weight,
          applies_to: criterion.applies_to,
        }))}
      />
    </div>
  );
}
