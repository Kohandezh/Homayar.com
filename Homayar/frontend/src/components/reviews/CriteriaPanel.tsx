import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Key } from "react";
import { Alert, Button, Card, Chip, Description, Input, Label, ListBox, Select, TextArea, TextField, toast } from "@heroui/react";

import { ErrorState, LoadingState } from "@/components/platform/Ui";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { reviewQueryKeys, useDecideReviewCriterion, useReviewCriteria } from "@/lib/review-api";
import { REVIEW_ERROR_CODES, REVIEWS_COPY } from "@/components/reviews/reviews-copy";
import {
  OVERRIDE_REASON_CODES,
  REVIEWER_OUTCOMES,
  isRevisionConflict,
  outcomeLabel,
  overrideReasonLabel,
  parseEvidenceTurns,
} from "@/components/reviews/review-model";
import type {
  OverrideReasonCode,
  ReviewCriterionEntry,
  ReviewerOutcome,
  ReviewScore,
} from "@/types/reviews";

/** The "not chosen yet" id of the override selects; never sent. */
const UNSET = "unset";

const NOTE_MAX = 2000;

function scoreText(score: number | null, lang: "fa" | "en") {
  return score === null ? "-" : formatNumber(score, lang, 1);
}

function percentText(value: number, lang: "fa" | "en") {
  return `${formatNumber(value, lang)}${lang === "fa" ? "٪" : "%"}`;
}

/** One of the two score blocks. The two scores are never combined (ADR 0053). */
function ScoreBlock({
  heading,
  score,
  final,
  copy,
  lang,
}: {
  heading: string;
  score: ReviewScore | null;
  /** Only the human score knows whether it is final or a preview. */
  final: boolean | null;
  copy: typeof REVIEWS_COPY["en"];
  lang: "fa" | "en";
}) {
  return (
    <Card<"section"> render={(props) => <section {...props} aria-label={heading} />}>
      <Card.Header>
        <Card.Title<"h3"> render={(props) => <h3 {...props} className="m-0 text-sm font-semibold" />}>
          {heading}
        </Card.Title>
        {final !== null && (
          <Chip color={final ? "success" : "warning"} variant="soft" size="sm" className="ms-2">
            <Chip.Label>{final ? copy.final : copy.preview}</Chip.Label>
          </Chip>
        )}
      </Card.Header>
      <Card.Content className="text-sm">
        <dl className="m-0 grid gap-x-4 gap-y-1 text-xs" style={{ gridTemplateColumns: "auto auto" }}>
          <dt className="text-muted">{copy.outcome}</dt>
          <dd className="m-0 font-medium">{score ? outcomeLabel(score.outcome, copy) : "-"}</dd>
          <dt className="text-muted">{copy.score}</dt>
          <dd className="m-0 font-medium">{scoreText(score?.score ?? null, lang)}</dd>
          <dt className="text-muted">{copy.passThreshold}</dt>
          <dd className="m-0 font-medium">{score ? percentText(score.pass_threshold, lang) : "-"}</dd>
        </dl>
        <p className="m-0 mt-2 text-xs text-muted">
          {score
            ? copy.weightsLine(
                formatNumber(score.applicable_weight, lang),
                formatNumber(score.earned_weight, lang),
                formatNumber(score.failed_weight, lang),
                formatNumber(score.insufficient_weight, lang),
              )
            : "-"}
        </p>
      </Card.Content>
    </Card>
  );
}

interface OverrideDraft {
  outcome: string;
  reason: string;
  note: string;
  evidence: string;
}

const EMPTY_DRAFT: OverrideDraft = { outcome: UNSET, reason: UNSET, note: "", evidence: "" };

/**
 * The criteria panel of one review item (HOM-265 AC4): the machine score and
 * the human score as two separate blocks, then every criterion with its
 * machine outcome and, for the assignee, the accept and override controls.
 */
export default function CriteriaPanel({
  itemId,
  itemStatus,
  isAssignee,
  canManage,
}: {
  itemId: string;
  itemStatus: string;
  isAssignee: boolean;
  canManage: boolean;
}) {
  const { lang } = useI18n();
  const copy = REVIEWS_COPY[lang];
  const queryClient = useQueryClient();
  const criteriaQuery = useReviewCriteria(itemId);
  const decideMutation = useDecideReviewCriterion();

  const [overrideFor, setOverrideFor] = useState<string | null>(null);
  const [draft, setDraft] = useState<OverrideDraft>(EMPTY_DRAFT);
  const [draftError, setDraftError] = useState<string | null>(null);

  const data = criteriaQuery.data ?? null;
  const canDecide =
    canManage && isAssignee && (itemStatus === "in_review") && (data?.status ?? itemStatus) === "in_review";

  function openOverride(criterion: ReviewCriterionEntry) {
    decideMutation.reset();
    setDraftError(null);
    // The machine outcome itself is never offered: an override changes it.
    setDraft({ ...EMPTY_DRAFT });
    setOverrideFor(criterion.criterion_id);
  }

  /**
   * A refused decision shows its mapped text; on a revision conflict the
   * sentence promises the latest version, so both the criteria view and the
   * item are actually loaded (the write moved the item's revision). The
   * console never repeats the write by itself.
   */
  function handleDecisionError(error: unknown) {
    if (isRevisionConflict(error)) {
      void criteriaQuery.refetch();
      void queryClient.invalidateQueries({ queryKey: reviewQueryKeys.item(itemId) });
    }
  }

  async function submitAccept(criterion: ReviewCriterionEntry) {
    if (!data) return;
    decideMutation.reset();
    try {
      await decideMutation.mutateAsync({
        id: itemId,
        criterionId: criterion.criterion_id,
        input: { expected_revision: data.revision, decision: "accept" },
      });
      toast.success(copy.decisionAccepted);
    } catch (error) {
      handleDecisionError(error);
      // The alert under the panel shows the mapped reason.
    }
  }

  async function submitOverride(criterion: ReviewCriterionEntry) {
    if (!data) return;
    // Client validation first: a blocked request never leaves the browser.
    if (draft.outcome === UNSET) {
      setDraftError(copy.validation.outcomeRequired);
      return;
    }
    if (draft.reason === UNSET) {
      setDraftError(copy.validation.reasonRequired);
      return;
    }
    const note = draft.note.trim();
    if (note.length === 0) {
      setDraftError(copy.validation.noteRequired);
      return;
    }
    if (note.length > NOTE_MAX) {
      setDraftError(copy.validation.noteTooLong);
      return;
    }
    const evidence = parseEvidenceTurns(draft.evidence);
    if (evidence.error === "required") {
      setDraftError(copy.validation.evidenceRequired);
      return;
    }
    if (evidence.error === "invalid") {
      setDraftError(copy.validation.evidenceInvalid);
      return;
    }
    if (evidence.error === "duplicate") {
      setDraftError(copy.validation.evidenceDuplicate);
      return;
    }
    if (evidence.error === "too_many") {
      setDraftError(copy.validation.evidenceTooMany);
      return;
    }
    setDraftError(null);
    decideMutation.reset();
    try {
      await decideMutation.mutateAsync({
        id: itemId,
        criterionId: criterion.criterion_id,
        input: {
          expected_revision: data.revision,
          decision: "override",
          reviewer_outcome: draft.outcome as ReviewerOutcome,
          reason_code: draft.reason as OverrideReasonCode,
          note,
          evidence_turn_indexes: evidence.values ?? [],
        },
      });
      toast.success(copy.decisionSaved);
      setOverrideFor(null);
      setDraft(EMPTY_DRAFT);
    } catch (error) {
      handleDecisionError(error);
      // The alert under the panel shows the mapped reason.
    }
  }

  if (criteriaQuery.isPending) {
    return <LoadingState label={copy.loading} />;
  }

  if (criteriaQuery.isError) {
    return <ErrorState error={criteriaQuery.error} codes={REVIEW_ERROR_CODES} fallback={copy.loadError} />;
  }

  if (!data) return null;

  // No scorecard result: no machine score, no criteria table, no decisions.
  // Starting and completing still work on the item itself.
  if (data.machine === null) {
    return (
      <Card<"section"> render={(props) => <section {...props} aria-label={copy.criteriaHeading} />}>
        <Card.Content>
          <p className="m-0 text-sm text-muted">{copy.noMachine}</p>
        </Card.Content>
      </Card>
    );
  }

  return (
    <Card<"section"> render={(props) => <section {...props} aria-label={copy.criteriaHeading} />} className="min-w-0">
      <Card.Header>
        <Card.Title<"h2"> render={(props) => <h2 {...props} className="m-0 text-base font-semibold" />}>
          {copy.criteriaHeading}
        </Card.Title>
      </Card.Header>
      <Card.Content className="min-w-0 space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <ScoreBlock heading={copy.machineScore} score={data.machine} final={null} copy={copy} lang={lang} />
          <ScoreBlock
            heading={copy.humanScore}
            score={data.human}
            final={data.human ? data.human.final : null}
            copy={copy}
            lang={lang}
          />
        </div>

        {decideMutation.isError && (
          <ErrorState error={decideMutation.error} codes={REVIEW_ERROR_CODES} fallback={copy.writeFailed} />
        )}

        <ul className="m-0 grid list-none gap-3 p-0">
          {data.criteria.map((criterion) => {
            const insufficient = criterion.machine.outcome === "insufficient_evidence";
            const decision = criterion.decision;
            return (
              <li
                key={criterion.criterion_id}
                className="rounded-xl border border-separator p-4"
                aria-label={criterion.criterion_id}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold text-foreground" dir="auto">
                    {criterion.criterion_id}
                  </span>
                  <span className="text-xs text-muted">
                    {copy.columnWeight}: {formatNumber(criterion.weight, lang)}
                  </span>
                  {criterion.auto_fail && (
                    <Chip color="danger" variant="soft" size="sm">
                      <Chip.Label>{copy.autoFail}</Chip.Label>
                    </Chip>
                  )}
                  <span className="text-xs text-muted">· {outcomeLabel(criterion.machine.outcome, copy)}</span>
                  {criterion.machine.reason && (
                    <span className="text-xs text-muted" dir="auto">
                      ({criterion.machine.reason})
                    </span>
                  )}
                  <span className="text-xs text-muted">
                    · {copy.evidenceCount(formatNumber(criterion.machine.evidence.length, lang))}
                  </span>
                </div>

                <div className="mt-2 text-sm">
                  {!criterion.reviewable ? (
                    <span className="text-xs text-muted">{copy.notReviewable}</span>
                  ) : decision ? (
                    <div className="flex flex-wrap items-center gap-2" dir="auto">
                      <Chip
                        color={decision.decision === "accept" ? "success" : "accent"}
                        variant="soft"
                        size="sm"
                      >
                        <Chip.Label>
                          {decision.decision === "accept" ? copy.accept : copy.override}
                        </Chip.Label>
                      </Chip>
                      <span>{outcomeLabel(decision.reviewer_outcome, copy)}</span>
                      {decision.reason_code && (
                        <span className="text-xs text-muted">· {overrideReasonLabel(decision.reason_code, copy)}</span>
                      )}
                      {decision.note && (
                        <span className="text-xs text-muted" dir="auto">
                          · {decision.note}
                        </span>
                      )}
                    </div>
                  ) : (
                    <span className="text-xs text-muted">{copy.noDecision}</span>
                  )}
                </div>

                {canDecide && criterion.reviewable && (
                  <div className="mt-3 space-y-3 border-t border-separator pt-3">
                    <div className="flex flex-wrap items-center gap-3">
                      <Button
                        size="sm"
                        variant="secondary"
                        isDisabled={insufficient || decideMutation.isPending}
                        onPress={() => void submitAccept(criterion)}
                      >
                        {copy.accept}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        isDisabled={decideMutation.isPending}
                        onPress={() => (overrideFor === criterion.criterion_id ? setOverrideFor(null) : openOverride(criterion))}
                      >
                        {copy.override}
                      </Button>
                      {insufficient && <span className="text-xs text-muted">{copy.acceptBlocked}</span>}
                    </div>

                    {overrideFor === criterion.criterion_id && (
                      <form
                        className="grid gap-3"
                        onSubmit={(event) => {
                          event.preventDefault();
                          void submitOverride(criterion);
                        }}
                        noValidate
                      >
                        <Select
                          className="w-full sm:w-56"
                          variant="secondary"
                          value={draft.outcome}
                          onChange={(key: Key | null) => {
                            if (typeof key === "string") setDraft((running) => ({ ...running, outcome: key }));
                          }}
                        >
                          <Label>{copy.reviewerOutcome}</Label>
                          <Select.Trigger>
                            <Select.Value />
                            <Select.Indicator />
                          </Select.Trigger>
                          <Select.Popover>
                            <ListBox>
                              <ListBox.Item key={UNSET} id={UNSET} textValue={copy.choose}>
                                {copy.choose}
                                <ListBox.ItemIndicator />
                              </ListBox.Item>
                              {REVIEWER_OUTCOMES.filter(
                                (outcome) =>
                                  outcome !== criterion.machine.outcome &&
                                  (outcome !== "not_applicable" || criterion.na_allowed),
                              ).map((outcome) => (
                                <ListBox.Item key={outcome} id={outcome} textValue={copy.outcomes[outcome]}>
                                  {copy.outcomes[outcome]}
                                  <ListBox.ItemIndicator />
                                </ListBox.Item>
                              ))}
                            </ListBox>
                          </Select.Popover>
                        </Select>

                        <Select
                          className="w-full sm:w-56"
                          variant="secondary"
                          value={draft.reason}
                          onChange={(key: Key | null) => {
                            if (typeof key === "string") setDraft((running) => ({ ...running, reason: key }));
                          }}
                        >
                          <Label>{copy.reasonCode}</Label>
                          <Select.Trigger>
                            <Select.Value />
                            <Select.Indicator />
                          </Select.Trigger>
                          <Select.Popover>
                            <ListBox>
                              <ListBox.Item key={UNSET} id={UNSET} textValue={copy.choose}>
                                {copy.choose}
                                <ListBox.ItemIndicator />
                              </ListBox.Item>
                              {OVERRIDE_REASON_CODES.map((code) => (
                                <ListBox.Item key={code} id={code} textValue={copy.overrideReasons[code]}>
                                  {copy.overrideReasons[code]}
                                  <ListBox.ItemIndicator />
                                </ListBox.Item>
                              ))}
                            </ListBox>
                          </Select.Popover>
                        </Select>

                        <TextField
                          variant="secondary"
                          value={draft.note}
                          onChange={(value) => setDraft((running) => ({ ...running, note: value }))}
                        >
                          <Label>{copy.note}</Label>
                          <TextArea dir="auto" rows={3} autoComplete="off" />
                          <Description>{copy.noteCounter(formatNumber(draft.note.length, lang), formatNumber(NOTE_MAX, lang))}</Description>
                        </TextField>

                        <TextField
                          variant="secondary"
                          value={draft.evidence}
                          onChange={(value) => setDraft((running) => ({ ...running, evidence: value }))}
                        >
                          <Label>{copy.evidenceTurns}</Label>
                          {/* A plain text field: numeric keypads have no comma,
                              and the value may hold several turns. */}
                          <Input dir="ltr" autoComplete="off" />
                          <Description>{copy.evidenceHint}</Description>
                        </TextField>

                        {draftError && (
                          <Alert status="danger" role="alert">
                            <Alert.Indicator aria-hidden="true" />
                            <Alert.Content>
                              <Alert.Description>{draftError}</Alert.Description>
                            </Alert.Content>
                          </Alert>
                        )}

                        <div className="flex gap-3">
                          <Button type="submit" size="sm" variant="primary" isPending={decideMutation.isPending}>
                            {copy.saveDecision}
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onPress={() => {
                              setOverrideFor(null);
                              setDraft(EMPTY_DRAFT);
                              setDraftError(null);
                            }}
                          >
                            {copy.cancelDecision}
                          </Button>
                        </div>
                      </form>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </Card.Content>
    </Card>
  );
}
