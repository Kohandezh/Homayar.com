import { describe, expect, it } from "vitest";
import {
  criterionApplicability,
  definitionFromDraft,
  draftApplicability,
  draftFromDefinition,
  newCriterionDraft,
  newScorecardDraft,
  splitLines,
  validateDraft,
  weightTotal,
  type ScorecardDraft,
} from "@/components/admin/scorecards/scorecard-model";
import type { ScorecardApplicability, ScorecardDefinition } from "@/types/scorecards";

function applies(applies_to: ScorecardApplicability | null, conversation_type: string, direction: string | null) {
  return criterionApplicability({ applies_to }, { conversation_type, direction });
}

/** A stored definition that uses every field the form hides. */
function storedDefinition(): ScorecardDefinition {
  return {
    name: "Inbound support",
    description: null,
    pass_threshold: 75,
    criteria: [
      {
        criterion_id: "greeting",
        label: "Greets the caller",
        weight: 60,
        auto_fail: false,
        na_allowed: false,
        applies_to: null,
        check: {
          check_id: "greeting",
          version: 3,
          kind: "required_phrase",
          phrases: ["hello", "good morning"],
          model_fallback: true,
          critical: true,
          speaker_role: "agent",
          scope: { kind: "opening", max_turns: 4 },
        },
      },
      {
        criterion_id: "consent",
        label: "Asks for recording consent",
        weight: 40,
        auto_fail: true,
        na_allowed: true,
        applies_to: { conversation_types: ["call"], directions: null },
        check: {
          check_id: "consent",
          version: 2,
          kind: "contextual",
          question: "Did the agent ask for consent?",
          trigger_phrases: ["record"],
          context_turns: 3,
          speaker_role: "any",
          scope: { kind: "whole_call" },
        },
      },
    ],
  };
}

function editDraft(): ScorecardDraft {
  return { ...draftFromDefinition(storedDefinition(), "inbound_support"), reason: "tune weights" };
}

describe("weightTotal", () => {
  it("adds the weights", () => {
    expect(weightTotal([])).toBe(0);
    expect(weightTotal([{ weight: 60 }, { weight: 40 }])).toBe(100);
    expect(weightTotal([{ weight: 50 }, { weight: 40 }])).toBe(90);
  });
});

describe("criterionApplicability", () => {
  it("applies everywhere without applies_to", () => {
    expect(applies(null, "meeting", null)).toBe("applies");
  });

  it("decides the type before the direction", () => {
    const rule = { conversation_types: ["call" as const], directions: ["inbound" as const] };
    expect(applies(rule, "meeting", null)).toBe("not_applicable");
    expect(applies(rule, "meeting", "inbound")).toBe("not_applicable");
    expect(applies(rule, "call", null)).toBe("unknown");
    expect(applies(rule, "call", "outbound")).toBe("not_applicable");
    expect(applies(rule, "call", "inbound")).toBe("applies");
  });

  it("treats a null group as all", () => {
    expect(applies({ conversation_types: null, directions: ["outbound"] }, "voicemail", "outbound")).toBe("applies");
    expect(applies({ conversation_types: null, directions: ["outbound"] }, "voicemail", null)).toBe("unknown");
    expect(applies({ conversation_types: ["other"], directions: null }, "other", null)).toBe("applies");
  });
});

describe("splitLines", () => {
  it("trims each line and drops empty ones", () => {
    expect(splitLines("  hello \n\n good morning\n  \n")).toEqual(["hello", "good morning"]);
    expect(splitLines("")).toEqual([]);
  });
});

describe("newScorecardDraft", () => {
  it("starts at threshold 80 with one required-phrase criterion of weight 100", () => {
    const draft = newScorecardDraft();
    expect(draft.pass_threshold).toBe("80");
    expect(draft.criteria).toHaveLength(1);
    expect(draft.criteria[0]).toMatchObject({
      weight: "100",
      kind: "required_phrase",
      speaker_role: "agent",
      scope_kind: "whole_call",
      auto_fail: false,
      na_allowed: false,
    });
  });
});

describe("definitionFromDraft", () => {
  it("sends an unchanged stored definition back exactly as it was", () => {
    expect(definitionFromDraft(editDraft())).toEqual(storedDefinition());
  });

  it("keeps the hidden fields when a shown field changes", () => {
    const draft = editDraft();
    draft.criteria[0] = { ...draft.criteria[0], weight: "50", phrases: "hello\nhi there" };
    draft.criteria[1] = { ...draft.criteria[1], weight: "50" };
    const definition = definitionFromDraft(draft);
    expect(definition.criteria[0].weight).toBe(50);
    expect(definition.criteria[0].check).toEqual({
      check_id: "greeting",
      version: 3,
      kind: "required_phrase",
      phrases: ["hello", "hi there"],
      model_fallback: true,
      critical: true,
      speaker_role: "agent",
      scope: { kind: "opening", max_turns: 4 },
    });
    expect(definition.criteria[1].check).toMatchObject({ version: 2, context_turns: 3 });
  });

  it("builds a new scorecard with check ids, version 1 and null groups", () => {
    const draft = newScorecardDraft();
    draft.scorecard_key = "sales_calls";
    draft.name = "  Sales calls ";
    draft.criteria[0] = {
      ...draft.criteria[0],
      criterion_id: "pitch",
      label: "Pitches the plan",
      weight: "70",
      phrases: " plan \n\n offer ",
      directions: ["outbound"],
    };
    draft.criteria.push({
      ...newCriterionDraft(),
      criterion_id: "polite",
      label: "Is polite",
      weight: "۳۰",
      kind: "contextual",
      question: "Was the agent polite?",
      trigger_phrases: "",
      scope_kind: "closing",
      max_turns: "2",
    });
    expect(definitionFromDraft(draft)).toEqual({
      name: "Sales calls",
      description: null,
      pass_threshold: 80,
      criteria: [
        {
          criterion_id: "pitch",
          label: "Pitches the plan",
          weight: 70,
          auto_fail: false,
          na_allowed: false,
          applies_to: { conversation_types: null, directions: ["outbound"] },
          check: {
            check_id: "pitch",
            version: 1,
            kind: "required_phrase",
            phrases: ["plan", "offer"],
            speaker_role: "agent",
            scope: { kind: "whole_call" },
          },
        },
        {
          criterion_id: "polite",
          label: "Is polite",
          weight: 30,
          auto_fail: false,
          na_allowed: false,
          applies_to: null,
          check: {
            check_id: "polite",
            version: 1,
            kind: "contextual",
            question: "Was the agent polite?",
            trigger_phrases: [],
            speaker_role: "agent",
            scope: { kind: "closing", max_turns: 2 },
          },
        },
      ],
    });
  });

  it("follows a renamed criterion id into the check id", () => {
    const draft = editDraft();
    draft.criteria[0] = { ...draft.criteria[0], criterion_id: "welcome" };
    expect(definitionFromDraft(draft).criteria[0].check.check_id).toBe("welcome");
  });

  it("drops the fields of the old kind when the check type changes", () => {
    const draft = editDraft();
    draft.criteria[0] = { ...draft.criteria[0], kind: "contextual", question: "Greeted?" };
    draft.criteria[1] = { ...draft.criteria[1], kind: "forbidden_phrase", phrases: "shut up" };
    const [first, second] = definitionFromDraft(draft).criteria;
    expect(first.check).toEqual({
      check_id: "greeting",
      version: 3,
      kind: "contextual",
      question: "Greeted?",
      trigger_phrases: [],
      critical: true,
      speaker_role: "agent",
      scope: { kind: "opening", max_turns: 4 },
    });
    expect(second.check).toEqual({
      check_id: "consent",
      version: 2,
      kind: "forbidden_phrase",
      phrases: ["shut up"],
      speaker_role: "any",
      scope: { kind: "whole_call" },
    });
  });

  it("sends no max_turns for a whole-call scope and a description only when typed", () => {
    const draft = editDraft();
    draft.description = "  For the support line  ";
    draft.criteria[0] = { ...draft.criteria[0], scope_kind: "whole_call" };
    const definition = definitionFromDraft(draft);
    expect(definition.description).toBe("For the support line");
    expect(definition.criteria[0].check.scope).toEqual({ kind: "whole_call" });
  });
});

describe("draftApplicability", () => {
  it("is null when nothing is checked", () => {
    expect(draftApplicability({ conversation_types: [], directions: [] })).toBeNull();
    expect(draftApplicability({ conversation_types: ["meeting", "call"], directions: [] })).toEqual({
      conversation_types: ["call", "meeting"],
      directions: null,
    });
  });
});

describe("validateDraft", () => {
  it("accepts the stored definition with a reason", () => {
    expect(validateDraft(editDraft(), { isNew: false })).toMatchObject({ canCheck: true, canPublish: true });
  });

  it("refuses a total that is not 100 for publish, not for check", () => {
    const draft = editDraft();
    draft.criteria[1] = { ...draft.criteria[1], weight: "30" };
    const result = validateDraft(draft, { isNew: false });
    expect(result.total).toBe(90);
    expect(result.totalWrong).toBe(true);
    expect(result.canPublish).toBe(false);
    expect(result.canCheck).toBe(true);
  });

  it("blocks check and publish on a value that is not a whole number", () => {
    const draft = editDraft();
    draft.criteria[0] = { ...draft.criteria[0], weight: "6.5" };
    const result = validateDraft(draft, { isNew: false });
    expect(result.criteria[0].weight).toBe("not_number");
    expect(result.canCheck).toBe(false);
    expect(result.canPublish).toBe(false);
  });

  it("blocks publish, not check, on values out of range", () => {
    const draft = editDraft();
    draft.pass_threshold = "0";
    draft.criteria[0] = { ...draft.criteria[0], weight: "160", scope_kind: "closing", max_turns: "0" };
    draft.criteria[1] = { ...draft.criteria[1], weight: "-60" };
    const result = validateDraft(draft, { isNew: false });
    expect(result.threshold).toBe("out_of_range");
    expect(result.criteria[0]).toEqual({ weight: "out_of_range", turns: "out_of_range" });
    // A minus sign is not a whole number the form can send.
    expect(result.criteria[1].weight).toBe("not_number");
    expect(result.canPublish).toBe(false);
  });

  it("asks for a reason and, for a new scorecard, a valid key", () => {
    const draft = newScorecardDraft();
    draft.reason = "   ";
    draft.scorecard_key = "Bad key";
    const result = validateDraft(draft, { isNew: true });
    expect(result.reason).toBe(true);
    expect(result.key).toBe(true);
    expect(result.canPublish).toBe(false);
    expect(result.canCheck).toBe(true);
  });

  it("ignores the turns of a whole-call scope", () => {
    const draft = editDraft();
    draft.criteria[0] = { ...draft.criteria[0], scope_kind: "whole_call", max_turns: "" };
    expect(validateDraft(draft, { isNew: false }).criteria[0].turns).toBeNull();
  });
});
