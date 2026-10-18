import { describe, expect, it } from "vitest";
import {
  AI_MODEL_ENDPOINT_KIND_ORDER,
  diffPolicies,
  draftFromPolicy,
  isValidRegion,
  parseRegions,
  policiesEqual,
  policyFromDraft,
  validateDraft,
  type PolicyDraft,
} from "@/components/admin/ai-policy/ai-policy-model";
import type { AiPolicy } from "@/types/ai-policy";

function stage(overrides: Partial<AiPolicy["asr"]> = {}): AiPolicy["asr"] {
  return {
    endpoint_kinds: ["in_process", "private"],
    regions: [],
    manifests: [],
    fallback: false,
    ...overrides,
  };
}

/** The demo default: every stage in-process and private, redacted cloud text. */
function policy(overrides: Partial<AiPolicy> = {}): AiPolicy {
  return {
    asr: stage(),
    diarization: stage(),
    intelligence: { ...stage(), cloud_text: "redacted" },
    ...overrides,
  };
}

function draft(current: AiPolicy, overrides: Partial<PolicyDraft> = {}): PolicyDraft {
  return { ...draftFromPolicy(current), ...overrides };
}

describe("parseRegions", () => {
  it("splits on commas and spaces, trims, drops empty parts", () => {
    expect(parseRegions("eu-west-1, me-central-1")).toEqual(["eu-west-1", "me-central-1"]);
    expect(parseRegions("  eu-west-1   me-central-1  ")).toEqual(["eu-west-1", "me-central-1"]);
    expect(parseRegions("eu-west-1,,  ,me-central-1")).toEqual(["eu-west-1", "me-central-1"]);
    expect(parseRegions(" , , ")).toEqual([]);
  });

  it("removes duplicates and keeps the letter case", () => {
    expect(parseRegions("eu-west-1, EU-west-1, eu-west-1")).toEqual(["eu-west-1", "EU-west-1"]);
  });
});

describe("isValidRegion", () => {
  it("accepts the server pattern and rejects unknown", () => {
    expect(isValidRegion("eu-west-1")).toBe(true);
    expect(isValidRegion("m1")).toBe(true);
    expect(isValidRegion("a".repeat(40))).toBe(true);
    for (const invalid of ["unknown", "EU-WEST-1", "eu_west", "-eu-west", "e", ""]) {
      expect(isValidRegion(invalid)).toBe(false);
    }
  });
});

describe("draftFromPolicy and policyFromDraft", () => {
  it("round-trips a policy through the draft", () => {
    const current = policy({
      asr: stage({ endpoint_kinds: ["cloud", "in_process"], regions: ["eu-west-1"], manifests: ["asr-openai"] }),
      intelligence: { ...stage({ fallback: true }), cloud_text: "original" },
    });
    expect(policyFromDraft(draftFromPolicy(current))).toEqual(current);
  });

  it("carries the reason separately and presets keep their own manifests", () => {
    const withReason = draftFromPolicy(policy(), "audit reason");
    expect(withReason.reason).toBe("audit reason");
    const preset = policy({ asr: stage({ endpoint_kinds: ["in_process"], manifests: ["asr-x"] }) });
    expect(policyFromDraft(draftFromPolicy(preset)).asr.manifests).toEqual(["asr-x"]);
  });

  it("builds kinds in the server's order and drops an empty stage to no kinds", () => {
    const result = policyFromDraft(
      draft(policy(), {
        asr: {
          in_process: false,
          private: false,
          cloud: true,
          regions: "eu-west-1",
          fallback: false,
          manifests: [],
          cloud_text: "redacted",
        },
      }),
    );
    expect(result.asr.endpoint_kinds).toEqual(["cloud"]);
    expect(AI_MODEL_ENDPOINT_KIND_ORDER).toEqual(["cloud", "in_process", "private"]);
    const emptied = policyFromDraft(
      draft(policy(), { diarization: { ...draftFromPolicy(policy()).diarization, in_process: false, private: false } }),
    );
    expect(emptied.diarization.endpoint_kinds).toEqual([]);
  });
});

describe("policiesEqual", () => {
  it("ignores list order but not content", () => {
    const a = policy({ asr: stage({ endpoint_kinds: ["private", "in_process"], regions: ["b", "a"] }) });
    const b = policy({ asr: stage({ endpoint_kinds: ["in_process", "private"], regions: ["a", "b"] }) });
    expect(policiesEqual(a, b)).toBe(true);
    expect(policiesEqual(a, policy({ asr: stage({ regions: ["a"] }) }))).toBe(false);
    expect(
      policiesEqual(a, policy({ intelligence: { ...stage(), cloud_text: "original" } })),
    ).toBe(false);
  });
});

describe("validateDraft", () => {
  it("accepts the current policy and both allow-controls", () => {
    expect(validateDraft(draftFromPolicy(policy(), "a good reason"))).toEqual({ stages: [], reason: false });
    const cloudWithRegion = draft(policy(), {
      asr: { ...draftFromPolicy(policy()).asr, cloud: true, regions: "eu-west-1" },
    });
    expect(validateDraft(cloudWithRegion).stages).toEqual([]);
    const localOnly = draft(policy(), {
      asr: { ...draftFromPolicy(policy()).asr, in_process: true, private: false, regions: "" },
    });
    expect(validateDraft(localOnly).stages).toEqual([]);
  });

  it("flags cloud without a region in that stage", () => {
    const result = validateDraft(
      draft(policy(), { asr: { ...draftFromPolicy(policy()).asr, cloud: true, regions: "  " } }),
    );
    expect(result.stages).toEqual([{ stage: "asr", kind: "cloud_needs_region" }]);
  });

  it("flags a region the server would refuse, and names the stage", () => {
    const result = validateDraft(
      draft(policy(), {
        diarization: { ...draftFromPolicy(policy()).diarization, cloud: true, regions: "eu-west-1, EU-west-1" },
      }),
    );
    expect(result.stages).toEqual([{ stage: "diarization", kind: "invalid_region" }]);
    const unknown = validateDraft(
      draft(policy(), { asr: { ...draftFromPolicy(policy()).asr, cloud: true, regions: "unknown" } }),
    );
    expect(unknown.stages).toEqual([{ stage: "asr", kind: "invalid_region" }]);
  });

  it("flags a reason shorter than 3 characters after trim", () => {
    expect(validateDraft(draft(policy(), { reason: "ok reason" })).reason).toBe(false);
    for (const short of ["", "  ", "ab", "  ab "]) {
      expect(validateDraft(draft(policy(), { reason: short })).reason).toBe(true);
    }
  });
});

describe("diffPolicies", () => {
  it("lists every differing field, stage by stage, and nothing else", () => {
    const selected = policy({
      asr: stage({ endpoint_kinds: ["in_process"], regions: ["eu-west-1"] }),
      intelligence: { ...stage({ fallback: true }), cloud_text: "original" },
    });
    const current = policy({
      asr: stage({ endpoint_kinds: ["in_process", "private"] }),
      diarization: stage({ manifests: ["diar-x"] }),
    });
    expect(diffPolicies(selected, current)).toEqual([
      { stage: "asr", field: "endpoint_kinds", selected: ["in_process"], current: ["in_process", "private"] },
      { stage: "asr", field: "regions", selected: ["eu-west-1"], current: [] },
      { stage: "diarization", field: "manifests", selected: [], current: ["diar-x"] },
      { stage: "intelligence", field: "fallback", selected: true, current: false },
      { stage: "intelligence", field: "cloud_text", selected: "original", current: "redacted" },
    ]);
  });

  it("returns an empty list for equal policies", () => {
    expect(diffPolicies(policy(), policy())).toEqual([]);
  });
});
