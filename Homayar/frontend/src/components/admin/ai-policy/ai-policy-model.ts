/**
 * Pure rules of the AI policy page: the draft the edit form holds, the region
 * text parsing, the client-side validation, the policy the draft saves, and
 * the diff behind "Compare version N". No React here, so every rule is tested
 * on its own. The server remains the authority: it sorts lists and rejects
 * extra keys, so the draft always builds a document with exactly the API keys.
 */
import {
  AI_POLICY_STAGES,
  type AiStageName,
} from "@/components/admin/ai-policy/ai-policy-copy";
import type {
  AiCloudTextRule,
  AiEndpointKind,
  AiPolicy,
  AiStagePolicy,
  AiIntelligenceStagePolicy,
} from "@/types/ai-policy";

/** The server sorts endpoint kinds by value (backend/providers/policy.py). */
export const AI_MODEL_ENDPOINT_KIND_ORDER: readonly AiEndpointKind[] = [
  "cloud",
  "in_process",
  "private",
];

/** A region the server accepts (backend/providers/policy.py _REGION). */
export const REGION_PATTERN = /^[a-z0-9][a-z0-9-]{1,39}$/;

export const REASON_MAX_LENGTH = 500;
export const REASON_MIN_LENGTH = 3;

export function isValidRegion(region: string): boolean {
  return REGION_PATTERN.test(region) && region !== "unknown";
}

/**
 * Regions as typed: split on commas or spaces, each part trimmed (letter case
 * untouched), empty parts dropped, duplicates removed.
 */
export function parseRegions(text: string): string[] {
  const regions: string[] = [];
  for (const part of text.split(/[,\s]+/)) {
    if (part && !regions.includes(part)) regions.push(part);
  }
  return regions;
}

/** The editable form state of one stage. */
export interface StageDraft {
  in_process: boolean;
  private: boolean;
  cloud: boolean;
  /** Raw text, so invalid values stay visible to the reader. */
  regions: string;
  fallback: boolean;
  /** Not edited in the form: kept from the current stage, or from a preset. */
  manifests: string[];
  /** Read only for the intelligence stage. */
  cloud_text: AiCloudTextRule;
}

export interface PolicyDraft {
  asr: StageDraft;
  diarization: StageDraft;
  intelligence: StageDraft;
  reason: string;
}

export function draftFromPolicy(policy: AiPolicy, reason = ""): PolicyDraft {
  const stage = (value: AiStagePolicy | AiIntelligenceStagePolicy): StageDraft => ({
    in_process: value.endpoint_kinds.includes("in_process"),
    private: value.endpoint_kinds.includes("private"),
    cloud: value.endpoint_kinds.includes("cloud"),
    regions: value.regions.join(", "),
    fallback: value.fallback,
    manifests: [...value.manifests],
    cloud_text: "cloud_text" in value ? value.cloud_text : "redacted",
  });
  return {
    asr: stage(policy.asr),
    diarization: stage(policy.diarization),
    intelligence: stage(policy.intelligence),
    reason,
  };
}

/** The document to save: exactly the keys the API accepts, lists sorted like the server. */
export function policyFromDraft(draft: PolicyDraft): AiPolicy {
  function stage(value: StageDraft): AiStagePolicy {
    // The kind ids are the draft's boolean keys, so the filter reads them directly.
    const kinds = AI_MODEL_ENDPOINT_KIND_ORDER.filter((kind) => value[kind]);
    return {
      endpoint_kinds: kinds,
      regions: [...parseRegions(value.regions)].sort(),
      manifests: [...value.manifests].sort(),
      fallback: value.fallback,
    };
  }
  return {
    asr: stage(draft.asr),
    diarization: stage(draft.diarization),
    intelligence: { ...stage(draft.intelligence), cloud_text: draft.intelligence.cloud_text },
  };
}

function sameItems(a: readonly string[], b: readonly string[]): boolean {
  if (a.length !== b.length) return false;
  const left = [...a].sort();
  const right = [...b].sort();
  return left.every((value, index) => value === right[index]);
}

function stagesEqual(
  a: AiStagePolicy | AiIntelligenceStagePolicy,
  b: AiStagePolicy | AiIntelligenceStagePolicy,
): boolean {
  return (
    sameItems(a.endpoint_kinds, b.endpoint_kinds) &&
    sameItems(a.regions, b.regions) &&
    sameItems(a.manifests, b.manifests) &&
    a.fallback === b.fallback
  );
}

/** Order-insensitive comparison, because the server sorts every list. */
export function policiesEqual(a: AiPolicy, b: AiPolicy): boolean {
  return (
    stagesEqual(a.asr, b.asr) &&
    stagesEqual(a.diarization, b.diarization) &&
    stagesEqual(a.intelligence, b.intelligence) &&
    a.intelligence.cloud_text === b.intelligence.cloud_text
  );
}

export type StageIssueKind = "cloud_needs_region" | "invalid_region";

export interface StageIssue {
  stage: AiStageName;
  kind: StageIssueKind;
}

export interface DraftValidation {
  stages: StageIssue[];
  /** True when the reason is shorter than 3 characters after trim. */
  reason: boolean;
}

export function validateDraft(draft: PolicyDraft): DraftValidation {
  const stages: StageIssue[] = [];
  for (const name of AI_POLICY_STAGES) {
    const value = draft[name];
    const regions = parseRegions(value.regions);
    if (value.cloud && regions.length === 0) {
      stages.push({ stage: name, kind: "cloud_needs_region" });
    }
    if (regions.some((region) => !isValidRegion(region))) {
      stages.push({ stage: name, kind: "invalid_region" });
    }
  }
  return { stages, reason: draft.reason.trim().length < REASON_MIN_LENGTH };
}

export type PolicyDiffField = "endpoint_kinds" | "regions" | "manifests" | "fallback" | "cloud_text";

export interface PolicyDiffEntry {
  stage: AiStageName;
  field: PolicyDiffField;
  selected: readonly string[] | boolean | AiCloudTextRule;
  current: readonly string[] | boolean | AiCloudTextRule;
}

function diffStage(
  name: AiStageName,
  selected: AiStagePolicy | AiIntelligenceStagePolicy,
  current: AiStagePolicy | AiIntelligenceStagePolicy,
): PolicyDiffEntry[] {
  const entries: PolicyDiffEntry[] = [];
  const push = (field: PolicyDiffField, a: PolicyDiffEntry["selected"], b: PolicyDiffEntry["current"]) =>
    entries.push({ stage: name, field, selected: a, current: b });
  if (!sameItems(selected.endpoint_kinds, current.endpoint_kinds)) {
    push("endpoint_kinds", [...selected.endpoint_kinds], [...current.endpoint_kinds]);
  }
  if (!sameItems(selected.regions, current.regions)) {
    push("regions", [...selected.regions], [...current.regions]);
  }
  if (!sameItems(selected.manifests, current.manifests)) {
    push("manifests", [...selected.manifests], [...current.manifests]);
  }
  if (selected.fallback !== current.fallback) {
    push("fallback", selected.fallback, current.fallback);
  }
  if (
    "cloud_text" in selected &&
    "cloud_text" in current &&
    selected.cloud_text !== current.cloud_text
  ) {
    push("cloud_text", selected.cloud_text, current.cloud_text);
  }
  return entries;
}

/** Every stage field that differs between a stored version and the current policy. */
export function diffPolicies(selected: AiPolicy, current: AiPolicy): PolicyDiffEntry[] {
  return [
    ...diffStage("asr", selected.asr, current.asr),
    ...diffStage("diarization", selected.diarization, current.diarization),
    ...diffStage("intelligence", selected.intelligence, current.intelligence),
  ];
}
