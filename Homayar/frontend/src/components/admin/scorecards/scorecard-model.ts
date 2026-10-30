/**
 * The scorecard editor's pure logic (HOM-248): the weight total, the
 * applicability rule of the preview, and the draft the form edits.
 *
 * The draft keeps every number as the text the reader typed, so a half-typed
 * value never turns into a silent zero. Each criterion also keeps the stored
 * criterion it came from: fields the form does not show (critical,
 * model_fallback, context_turns, the check version, an untouched scope) go
 * back to the server exactly as they were read.
 */
import { parseWholeNumber } from "@/lib/format";
import type {
  ConversationDirection,
  ConversationType,
  ScorecardApplicability,
  ScorecardCheck,
  ScorecardCheckKind,
  ScorecardCheckScope,
  ScorecardContextualCheck,
  ScorecardCriterion,
  ScorecardDefinition,
  ScorecardPhraseCheck,
  ScorecardScopeKind,
  SpeakerRole,
} from "@/types/scorecards";

/** Scorecard keys and criterion ids (the server's KEY pattern). */
export const KEY_PATTERN = /^[a-z][a-z0-9_]{1,63}$/;
export const REASON_MAX_LENGTH = 500;

export const CONVERSATION_TYPES = ["call", "meeting", "voicemail", "other"] as const satisfies readonly ConversationType[];
export const DIRECTIONS = ["inbound", "outbound", "internal"] as const satisfies readonly ConversationDirection[];
export const CHECK_KINDS = ["required_phrase", "forbidden_phrase", "contextual"] as const satisfies readonly ScorecardCheckKind[];
export const SPEAKER_ROLES = ["agent", "customer", "any"] as const satisfies readonly SpeakerRole[];
export const SCOPE_KINDS = ["whole_call", "opening", "closing"] as const satisfies readonly ScorecardScopeKind[];

export type Applicability = "applies" | "not_applicable" | "unknown";

export function weightTotal(criteria: readonly { weight: number }[]): number {
  return criteria.reduce((sum, criterion) => sum + criterion.weight, 0);
}

/**
 * Whether one criterion applies to one conversation. The type is decided
 * before the direction; a direction that is not known is "unknown" only when
 * the criterion limits directions.
 */
export function criterionApplicability(
  criterion: { applies_to: ScorecardApplicability | null },
  conversation: { conversation_type: string; direction: string | null },
): Applicability {
  const rule = criterion.applies_to;
  if (rule === null) return "applies";
  if (rule.conversation_types !== null && !(rule.conversation_types as string[]).includes(conversation.conversation_type)) {
    return "not_applicable";
  }
  if (rule.directions !== null) {
    if (conversation.direction === null) return "unknown";
    if (!(rule.directions as string[]).includes(conversation.direction)) return "not_applicable";
  }
  return "applies";
}

/** One entry per line, trimmed, empty lines dropped. */
export function splitLines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

export interface CriterionDraft {
  /** Local id for React keys; never sent. */
  uid: string;
  criterion_id: string;
  label: string;
  weight: string;
  auto_fail: boolean;
  na_allowed: boolean;
  /** Nothing checked in a group means "all" for that group. */
  conversation_types: ConversationType[];
  directions: ConversationDirection[];
  kind: ScorecardCheckKind;
  /** One per line. */
  phrases: string;
  question: string;
  /** One per line. */
  trigger_phrases: string;
  speaker_role: SpeakerRole;
  scope_kind: ScorecardScopeKind;
  max_turns: string;
  /** The stored criterion this draft started from; null for a new one. */
  base: ScorecardCriterion | null;
}

export interface ScorecardDraft {
  /** Only typed for a new scorecard. */
  scorecard_key: string;
  name: string;
  description: string;
  pass_threshold: string;
  reason: string;
  criteria: CriterionDraft[];
}

let nextUid = 0;
function uid() {
  nextUid += 1;
  return `criterion-${nextUid}`;
}

export function newCriterionDraft(weight = ""): CriterionDraft {
  return {
    uid: uid(),
    criterion_id: "",
    label: "",
    weight,
    auto_fail: false,
    na_allowed: false,
    conversation_types: [],
    directions: [],
    kind: "required_phrase",
    phrases: "",
    question: "",
    trigger_phrases: "",
    speaker_role: "agent",
    scope_kind: "whole_call",
    max_turns: "1",
    base: null,
  };
}

/** A new scorecard: threshold 80 and one criterion that carries all the weight. */
export function newScorecardDraft(): ScorecardDraft {
  return {
    scorecard_key: "",
    name: "",
    description: "",
    pass_threshold: "80",
    reason: "",
    criteria: [newCriterionDraft("100")],
  };
}

function criterionDraft(criterion: ScorecardCriterion): CriterionDraft {
  const { check } = criterion;
  const scope = check.scope ?? { kind: "whole_call" };
  return {
    uid: uid(),
    criterion_id: criterion.criterion_id,
    label: criterion.label,
    weight: String(criterion.weight),
    auto_fail: criterion.auto_fail,
    na_allowed: criterion.na_allowed,
    conversation_types: [...(criterion.applies_to?.conversation_types ?? [])],
    directions: [...(criterion.applies_to?.directions ?? [])],
    kind: check.kind,
    phrases: check.kind === "contextual" ? "" : check.phrases.join("\n"),
    question: check.kind === "contextual" ? check.question : "",
    trigger_phrases: check.kind === "contextual" ? (check.trigger_phrases ?? []).join("\n") : "",
    speaker_role: check.speaker_role ?? "agent",
    scope_kind: scope.kind,
    max_turns: scope.kind === "whole_call" ? "1" : String(scope.max_turns),
    base: criterion,
  };
}

/** The draft of an edit: the stored definition, an empty reason. */
export function draftFromDefinition(definition: ScorecardDefinition, key: string): ScorecardDraft {
  return {
    scorecard_key: key,
    name: definition.name,
    description: definition.description ?? "",
    pass_threshold: String(definition.pass_threshold),
    reason: "",
    criteria: definition.criteria.map(criterionDraft),
  };
}

/** The applies_to of a draft criterion, groups in a fixed order; null when nothing is checked. */
export function draftApplicability(
  criterion: Pick<CriterionDraft, "conversation_types" | "directions">,
): ScorecardApplicability | null {
  const types = CONVERSATION_TYPES.filter((type) => criterion.conversation_types.includes(type));
  const directions = DIRECTIONS.filter((direction) => criterion.directions.includes(direction));
  if (types.length === 0 && directions.length === 0) return null;
  return {
    conversation_types: types.length > 0 ? types : null,
    directions: directions.length > 0 ? directions : null,
  };
}

function sameGroup(left: readonly string[] | null, right: readonly string[] | null) {
  if (left === null || right === null) return left === right;
  return left.length === right.length && left.every((value) => right.includes(value));
}

function sameApplicability(left: ScorecardApplicability | null, right: ScorecardApplicability | null) {
  if (left === null || right === null) return left === right;
  return (
    sameGroup(left.conversation_types, right.conversation_types) &&
    sameGroup(left.directions, right.directions)
  );
}

/** A weight for the live total and the preview; a value that is not a number counts as 0. */
export function draftWeight(criterion: Pick<CriterionDraft, "weight">): number {
  return parseWholeNumber(criterion.weight) ?? 0;
}

function scopeFromDraft(criterion: CriterionDraft): ScorecardCheckScope | undefined {
  const stored = criterion.base?.check.scope;
  const storedKind = stored?.kind ?? "whole_call";
  const turns = parseWholeNumber(criterion.max_turns) ?? 0;
  if (criterion.base) {
    // An untouched scope goes back exactly as read, absent or present.
    if (criterion.scope_kind === "whole_call" && storedKind === "whole_call") return stored;
    if (stored && stored.kind !== "whole_call" && stored.kind === criterion.scope_kind && stored.max_turns === turns) {
      return stored;
    }
  }
  return criterion.scope_kind === "whole_call"
    ? { kind: "whole_call" }
    : { kind: criterion.scope_kind, max_turns: turns };
}

function checkFromDraft(criterion: CriterionDraft): ScorecardCheck {
  const stored = criterion.base?.check ?? null;
  const common: Pick<ScorecardCheck, "check_id" | "version" | "critical" | "speaker_role" | "scope"> = {
    check_id: criterion.criterion_id,
    // A new criterion's check starts at version 1; a stored one keeps its own.
    version: stored?.version ?? 1,
  };
  if (stored?.critical !== undefined) common.critical = stored.critical;
  // A speaker the reader left alone keeps the stored form (absent means "agent").
  common.speaker_role =
    stored && (stored.speaker_role ?? "agent") === criterion.speaker_role ? stored.speaker_role : criterion.speaker_role;
  if (common.speaker_role === undefined) delete common.speaker_role;
  common.scope = scopeFromDraft(criterion);
  if (common.scope === undefined) delete common.scope;

  if (criterion.kind === "contextual") {
    const triggers = splitLines(criterion.trigger_phrases);
    const check: ScorecardContextualCheck = { ...common, kind: "contextual", question: criterion.question.trim() };
    const storedContextual = stored?.kind === "contextual" ? stored : null;
    if (!(storedContextual && storedContextual.trigger_phrases === undefined && triggers.length === 0)) {
      check.trigger_phrases = triggers;
    }
    if (storedContextual?.context_turns !== undefined) check.context_turns = storedContextual.context_turns;
    return check;
  }
  const check: ScorecardPhraseCheck = { ...common, kind: criterion.kind, phrases: splitLines(criterion.phrases) };
  if (stored && stored.kind !== "contextual" && stored.model_fallback !== undefined) {
    check.model_fallback = stored.model_fallback;
  }
  return check;
}

function criterionFromDraft(criterion: CriterionDraft): ScorecardCriterion {
  const appliesTo = draftApplicability(criterion);
  const storedAppliesTo = criterion.base?.applies_to;
  return {
    criterion_id: criterion.criterion_id,
    label: criterion.label.trim(),
    weight: draftWeight(criterion),
    auto_fail: criterion.auto_fail,
    na_allowed: criterion.na_allowed,
    // The same groups in another order are not a change.
    applies_to:
      storedAppliesTo !== undefined && sameApplicability(storedAppliesTo, appliesTo) ? storedAppliesTo : appliesTo,
    check: checkFromDraft(criterion),
  };
}

/**
 * The definition the draft stands for. Call it only when `validateDraft`
 * says `canCheck`: every number is then a whole number.
 */
export function definitionFromDraft(draft: ScorecardDraft): ScorecardDefinition {
  const description = draft.description.trim();
  return {
    name: draft.name.trim(),
    description: description.length > 0 ? description : null,
    pass_threshold: parseWholeNumber(draft.pass_threshold) ?? 0,
    criteria: draft.criteria.map(criterionFromDraft),
  };
}

export type NumberIssue = "not_number" | "out_of_range";

function numberIssue(text: string, min: number, max: number): NumberIssue | null {
  const value = parseWholeNumber(text);
  if (value === null) return "not_number";
  return value < min || value > max ? "out_of_range" : null;
}

export interface CriterionIssues {
  weight: NumberIssue | null;
  turns: NumberIssue | null;
}

export interface DraftValidation {
  total: number;
  totalWrong: boolean;
  threshold: NumberIssue | null;
  /** The reason is blank or too long. */
  reason: boolean;
  /** A new scorecard's key does not match KEY_PATTERN (it is part of the URL). */
  key: boolean;
  criteria: CriterionIssues[];
  /** No value that cannot be sent as an integer: `Check` may ask the server. */
  canCheck: boolean;
  /** Every client check passes: `Publish` may send. */
  canPublish: boolean;
}

/**
 * The client checks. Only what the form must decide itself: the total, whole
 * numbers in range, a reason, and a key that fits in the URL. The server
 * decides everything else and its 422 is shown.
 */
export function validateDraft(draft: ScorecardDraft, { isNew }: { isNew: boolean }): DraftValidation {
  const criteria = draft.criteria.map((criterion) => ({
    weight: numberIssue(criterion.weight, 0, 100),
    turns: criterion.scope_kind === "whole_call" ? null : numberIssue(criterion.max_turns, 1, 999_999),
  }));
  const threshold = numberIssue(draft.pass_threshold, 1, 100);
  const total = weightTotal(draft.criteria.map((criterion) => ({ weight: draftWeight(criterion) })));
  const reasonLength = draft.reason.trim().length;
  const reason = reasonLength === 0 || reasonLength > REASON_MAX_LENGTH;
  const key = isNew && !KEY_PATTERN.test(draft.scorecard_key);
  const issues = [threshold, ...criteria.flatMap((criterion) => [criterion.weight, criterion.turns])];
  const canCheck = !issues.includes("not_number");
  const canPublish = canCheck && issues.every((issue) => issue === null) && total === 100 && !reason && !key;
  return { total, totalWrong: total !== 100, threshold, reason, key, criteria, canCheck, canPublish };
}
