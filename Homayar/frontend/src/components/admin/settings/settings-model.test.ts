import { describe, expect, it } from "vitest";
import {
  PII_REDACTION_CLASSES,
  buildSettingsPatch,
  draftFrom,
  retentionShrinks,
  validateDraft,
  type SettingsDraft,
} from "@/components/admin/settings/settings-model";
import type { WorkspaceSettings } from "@/types/platform";

function settings(overrides: Partial<WorkspaceSettings> = {}): WorkspaceSettings {
  return {
    id: "ws-1",
    name: "Acme",
    retention_days: 30,
    retention_mode: "finite",
    delete_audio_after_processing: false,
    record_scope: "workspace",
    public_share_links_enabled: true,
    organization_share_links_enabled: true,
    duplicate_media_policy: "allow",
    pii_redaction_enabled: true,
    pii_redaction_classes: ["bank_card", "email", "iban", "ir_national_id", "ir_postal_code", "phone"],
    updated_at: "2026-10-14T10:00:00Z",
    ...overrides,
  };
}

function draft(current: WorkspaceSettings, overrides: Partial<SettingsDraft> = {}): SettingsDraft {
  return { ...draftFrom(current, "en"), ...overrides };
}

const OWNER = { canEditPii: true };
const ADMIN = { canEditPii: false };

describe("draftFrom", () => {
  it("copies every field and keeps the classes in a new array", () => {
    const current = settings();
    const result = draftFrom(current, "en");
    expect(result).toEqual({
      name: "Acme",
      retention_mode: "finite",
      retention_days: "30",
      delete_audio_after_processing: false,
      record_scope: "workspace",
      public_share_links_enabled: true,
      organization_share_links_enabled: true,
      duplicate_media_policy: "allow",
      pii_redaction_enabled: true,
      pii_redaction_classes: current.pii_redaction_classes,
    });
    expect(result.pii_redaction_classes).not.toBe(current.pii_redaction_classes);
  });

  it("shows the days in Persian digits in fa", () => {
    expect(draftFrom(settings({ retention_days: 90 }), "fa").retention_days).toBe("۹۰");
  });

  it("maps legacy_unresolved to an empty mode and 0 days to empty text", () => {
    const result = draftFrom(settings({ retention_mode: "legacy_unresolved", retention_days: 0 }), "en");
    expect(result.retention_mode).toBe("");
    expect(result.retention_days).toBe("");
  });
});

describe("validateDraft", () => {
  const current = settings();

  it("accepts the server values", () => {
    expect(validateDraft(draft(current))).toEqual([]);
  });

  it("rejects an empty or too long name, accepts 180 characters", () => {
    expect(validateDraft(draft(current, { name: "   " }))).toEqual(["name"]);
    expect(validateDraft(draft(current, { name: "a".repeat(181) }))).toEqual(["name"]);
    expect(validateDraft(draft(current, { name: "a".repeat(180) }))).toEqual([]);
  });

  it("checks the days only under the finite mode", () => {
    for (const valid of ["1", "3650", "۱۴"]) {
      expect(validateDraft(draft(current, { retention_days: valid }))).toEqual([]);
    }
    for (const invalid of ["0", "3651", "abc", ""]) {
      expect(validateDraft(draft(current, { retention_days: invalid }))).toEqual(["retention_days"]);
    }
    expect(validateDraft(draft(current, { retention_mode: "indefinite", retention_days: "" }))).toEqual([]);
    expect(validateDraft(draft(current, { retention_mode: "immediate", retention_days: "abc" }))).toEqual([]);
    expect(validateDraft(draft(current, { retention_mode: "", retention_days: "0" }))).toEqual([]);
  });

  it("rejects an empty class list, accepts one class", () => {
    expect(validateDraft(draft(current, { pii_redaction_classes: [] }))).toEqual(["pii_redaction_classes"]);
    expect(validateDraft(draft(current, { pii_redaction_classes: ["email"] }))).toEqual([]);
  });

  it("reports every invalid field", () => {
    expect(validateDraft(draft(current, { name: "", retention_days: "0", pii_redaction_classes: [] }))).toEqual([
      "name",
      "retention_days",
      "pii_redaction_classes",
    ]);
  });
});

describe("buildSettingsPatch", () => {
  it("returns {} for an unchanged draft", () => {
    const current = settings();
    expect(buildSettingsPatch(current, draft(current), OWNER)).toEqual({});
  });

  it("sends a trimmed name and ignores a whitespace-only change", () => {
    const current = settings();
    expect(buildSettingsPatch(current, draft(current, { name: "  Acme  " }), OWNER)).toEqual({});
    expect(buildSettingsPatch(current, draft(current, { name: "  New name " }), OWNER)).toEqual({ name: "New name" });
  });

  it("sends only the changed boolean and enum fields", () => {
    const current = settings();
    expect(buildSettingsPatch(current, draft(current, { public_share_links_enabled: false }), OWNER)).toEqual({
      public_share_links_enabled: false,
    });
    expect(
      buildSettingsPatch(
        current,
        draft(current, {
          organization_share_links_enabled: false,
          delete_audio_after_processing: true,
          record_scope: "team",
          duplicate_media_policy: "reject",
        }),
        OWNER,
      ),
    ).toEqual({
      organization_share_links_enabled: false,
      delete_audio_after_processing: true,
      record_scope: "team",
      duplicate_media_policy: "reject",
    });
  });

  it("sends nothing about retention for a legacy workspace with no mode chosen", () => {
    const current = settings({ retention_mode: "legacy_unresolved", retention_days: 0 });
    expect(buildSettingsPatch(current, draft(current, { retention_days: "14" }), OWNER)).toEqual({});
  });

  it("sends mode and days when a legacy workspace picks finite", () => {
    const current = settings({ retention_mode: "legacy_unresolved", retention_days: 0 });
    expect(buildSettingsPatch(current, draft(current, { retention_mode: "finite", retention_days: "۱۴" }), OWNER)).toEqual({
      retention_mode: "finite",
      retention_days: 14,
    });
  });

  it("sends mode and days when the mode changes to finite, even with the same days", () => {
    const current = settings({ retention_mode: "indefinite", retention_days: 0 });
    expect(buildSettingsPatch(current, draft(current, { retention_mode: "finite", retention_days: "30" }), OWNER)).toEqual({
      retention_mode: "finite",
      retention_days: 30,
    });
  });

  it("sends only the days for a days change under finite", () => {
    const current = settings();
    expect(buildSettingsPatch(current, draft(current, { retention_days: "60" }), OWNER)).toEqual({ retention_days: 60 });
    expect(buildSettingsPatch(current, draft(current, { retention_days: "۳۰" }), OWNER)).toEqual({});
  });

  it("never sends days for a non-finite mode", () => {
    const current = settings();
    expect(buildSettingsPatch(current, draft(current, { retention_mode: "indefinite", retention_days: "60" }), OWNER)).toEqual({
      retention_mode: "indefinite",
    });
    expect(buildSettingsPatch(current, draft(current, { retention_mode: "immediate" }), OWNER)).toEqual({
      retention_mode: "immediate",
    });
  });

  it("compares the classes as a set and sends them in canonical order", () => {
    const current = settings({ pii_redaction_classes: ["email", "phone"] });
    expect(buildSettingsPatch(current, draft(current, { pii_redaction_classes: ["phone", "email", "phone"] }), OWNER)).toEqual({});
    expect(
      buildSettingsPatch(current, draft(current, { pii_redaction_classes: ["phone", "iban", "email", "iban"] }), OWNER),
    ).toEqual({ pii_redaction_classes: ["email", "iban", "phone"] });
  });

  it("sends the PII keys for the owner and never for an admin", () => {
    const current = settings();
    const changed = draft(current, { pii_redaction_enabled: false, pii_redaction_classes: ["email"] });
    expect(buildSettingsPatch(current, changed, OWNER)).toEqual({
      pii_redaction_enabled: false,
      pii_redaction_classes: ["email"],
    });
    expect(buildSettingsPatch(current, changed, ADMIN)).toEqual({});
    expect(buildSettingsPatch(current, { ...changed, record_scope: "team" }, ADMIN)).toEqual({ record_scope: "team" });
  });

  it("never carries undefined or null values", () => {
    const current = settings();
    const patch = buildSettingsPatch(current, draft(current, { name: "X", retention_mode: "immediate" }), OWNER);
    for (const value of Object.values(patch)) {
      expect(value).not.toBeUndefined();
      expect(value).not.toBeNull();
    }
  });

  it("keeps the canonical class order of the server", () => {
    expect(PII_REDACTION_CLASSES).toEqual(["bank_card", "email", "iban", "ir_national_id", "ir_postal_code", "phone"]);
  });
});

describe("retentionShrinks", () => {
  const cases: [string, Partial<WorkspaceSettings>, Partial<SettingsDraft>, boolean][] = [
    ["finite -> immediate", {}, { retention_mode: "immediate" }, true],
    ["indefinite -> immediate", { retention_mode: "indefinite", retention_days: 0 }, { retention_mode: "immediate" }, true],
    ["legacy -> immediate", { retention_mode: "legacy_unresolved", retention_days: 0 }, { retention_mode: "immediate" }, true],
    ["indefinite -> finite", { retention_mode: "indefinite", retention_days: 0 }, { retention_mode: "finite", retention_days: "30" }, true],
    ["legacy -> finite", { retention_mode: "legacy_unresolved", retention_days: 0 }, { retention_mode: "finite", retention_days: "30" }, true],
    ["finite fewer days", {}, { retention_days: "10" }, true],
    ["finite fewer Persian days", {}, { retention_days: "۱۰" }, true],
    ["no change", {}, {}, false],
    ["finite more days", {}, { retention_days: "60" }, false],
    ["finite invalid days", {}, { retention_days: "abc" }, false],
    ["finite empty days", {}, { retention_days: "" }, false],
    ["finite -> indefinite", {}, { retention_mode: "indefinite" }, false],
    ["immediate -> immediate", { retention_mode: "immediate", retention_days: 0 }, {}, false],
    ["immediate -> finite", { retention_mode: "immediate", retention_days: 0 }, { retention_mode: "finite", retention_days: "5" }, false],
    ["immediate -> indefinite", { retention_mode: "immediate", retention_days: 0 }, { retention_mode: "indefinite" }, false],
    ["legacy, mode not chosen", { retention_mode: "legacy_unresolved", retention_days: 0 }, { retention_mode: "" }, false],
  ];

  it.each(cases)("%s", (_label, server, change, expected) => {
    const current = settings(server);
    expect(retentionShrinks(current, draft(current, change))).toBe(expected);
  });
});
