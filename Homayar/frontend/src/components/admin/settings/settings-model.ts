/**
 * Pure rules of the workspace settings form: the draft the form edits, its
 * validation, and the PATCH body that carries only what changed. No React here,
 * so every rule can be tested on its own.
 */
import { localizeDigits, parseWholeNumber } from "@/lib/format";
import type { Lang } from "@/lib/i18n-context";
import type {
  DuplicateMediaPolicy,
  PiiRedactionClass,
  RecordScope,
  UpdateWorkspaceSettingsShape,
  WorkspaceSettings,
} from "@/types/platform";

/** Canonical order = the server's order (sorted by value). */
export const PII_REDACTION_CLASSES: readonly PiiRedactionClass[] = [
  "bank_card",
  "email",
  "iban",
  "ir_national_id",
  "ir_postal_code",
  "phone",
];

export const NAME_MAX_LENGTH = 180;
export const RETENTION_DAYS_MIN = 1;
export const RETENTION_DAYS_MAX = 3650;

export interface SettingsDraft {
  /** As typed, not trimmed. */
  name: string;
  /** "" means a legacy workspace where no mode is chosen yet. */
  retention_mode: "finite" | "indefinite" | "immediate" | "";
  /** Raw text, so Persian or Latin digits and invalid text stay as typed. */
  retention_days: string;
  delete_audio_after_processing: boolean;
  record_scope: RecordScope;
  public_share_links_enabled: boolean;
  organization_share_links_enabled: boolean;
  duplicate_media_policy: DuplicateMediaPolicy;
  pii_redaction_enabled: boolean;
  /** Any order. */
  pii_redaction_classes: PiiRedactionClass[];
}

export type SettingsField = "name" | "retention_days" | "pii_redaction_classes";

/** The form values for the server settings. */
export function draftFrom(settings: WorkspaceSettings, lang: Lang): SettingsDraft {
  return {
    name: settings.name,
    // The legacy value is never shown as a real choice: until the administrator
    // picks a mode, no retention change is sent.
    retention_mode: settings.retention_mode === "legacy_unresolved" ? "" : settings.retention_mode,
    // Zero is not a valid period, so it shows as an empty field.
    retention_days: settings.retention_days === 0 ? "" : localizeDigits(String(settings.retention_days), lang),
    delete_audio_after_processing: settings.delete_audio_after_processing,
    record_scope: settings.record_scope,
    public_share_links_enabled: settings.public_share_links_enabled,
    organization_share_links_enabled: settings.organization_share_links_enabled,
    duplicate_media_policy: settings.duplicate_media_policy,
    pii_redaction_enabled: settings.pii_redaction_enabled,
    pii_redaction_classes: [...settings.pii_redaction_classes],
  };
}

/** The days as a number when they are a whole number from 1 to 3650, else null. */
function validDays(text: string): number | null {
  const days = parseWholeNumber(text);
  return days !== null && days >= RETENTION_DAYS_MIN && days <= RETENTION_DAYS_MAX ? days : null;
}

/** The invalid fields of the draft; empty when the draft can be saved. */
export function validateDraft(draft: SettingsDraft): SettingsField[] {
  const invalid: SettingsField[] = [];
  const name = draft.name.trim();
  if (!name || name.length > NAME_MAX_LENGTH) invalid.push("name");
  if (draft.retention_mode === "finite" && validDays(draft.retention_days) === null) invalid.push("retention_days");
  if (draft.pii_redaction_classes.length === 0) invalid.push("pii_redaction_classes");
  return invalid;
}

/** The classes without duplicates, in the server's order. */
export function canonicalClasses(classes: readonly PiiRedactionClass[]): PiiRedactionClass[] {
  const chosen = new Set(classes);
  return PII_REDACTION_CLASSES.filter((value) => chosen.has(value));
}

function sameClasses(a: readonly PiiRedactionClass[], b: readonly PiiRedactionClass[]): boolean {
  const left = canonicalClasses(a);
  const right = canonicalClasses(b);
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function retentionModeChanged(settings: WorkspaceSettings, draft: SettingsDraft): boolean {
  return (
    draft.retention_mode !== "" &&
    (settings.retention_mode === "legacy_unresolved" || draft.retention_mode !== settings.retention_mode)
  );
}

/**
 * The PATCH body: only the fields that differ from the server, `{}` when
 * nothing changed. The PII keys are left out when the caller may not change them.
 */
export function buildSettingsPatch(
  settings: WorkspaceSettings,
  draft: SettingsDraft,
  options: { canEditPii: boolean },
): Partial<UpdateWorkspaceSettingsShape> {
  const patch: Partial<UpdateWorkspaceSettingsShape> = {};

  const name = draft.name.trim();
  if (name !== settings.name) patch.name = name;

  // The server refuses days alone when the mode changes to finite, so a mode
  // change to finite always sends both keys.
  const modeChanged = retentionModeChanged(settings, draft);
  if (modeChanged && draft.retention_mode !== "") patch.retention_mode = draft.retention_mode;
  if (draft.retention_mode === "finite") {
    const days = parseWholeNumber(draft.retention_days);
    if (days !== null && (modeChanged || days !== settings.retention_days)) patch.retention_days = days;
  }

  if (draft.delete_audio_after_processing !== settings.delete_audio_after_processing) {
    patch.delete_audio_after_processing = draft.delete_audio_after_processing;
  }
  if (draft.record_scope !== settings.record_scope) patch.record_scope = draft.record_scope;
  if (draft.public_share_links_enabled !== settings.public_share_links_enabled) {
    patch.public_share_links_enabled = draft.public_share_links_enabled;
  }
  if (draft.organization_share_links_enabled !== settings.organization_share_links_enabled) {
    patch.organization_share_links_enabled = draft.organization_share_links_enabled;
  }
  if (draft.duplicate_media_policy !== settings.duplicate_media_policy) {
    patch.duplicate_media_policy = draft.duplicate_media_policy;
  }

  // Only the owner holds pii:original_access; the server answers 403 to
  // anyone else who sends a PII key.
  if (options.canEditPii) {
    if (draft.pii_redaction_enabled !== settings.pii_redaction_enabled) {
      patch.pii_redaction_enabled = draft.pii_redaction_enabled;
    }
    if (!sameClasses(draft.pii_redaction_classes, settings.pii_redaction_classes)) {
      patch.pii_redaction_classes = canonicalClasses(draft.pii_redaction_classes);
    }
  }

  return patch;
}

/** True when saving the draft would delete data sooner than the server does today. */
export function retentionShrinks(settings: WorkspaceSettings, draft: SettingsDraft): boolean {
  const current = settings.retention_mode;
  if (draft.retention_mode === "immediate") return current !== "immediate";
  if (draft.retention_mode !== "finite") return false;
  if (current === "indefinite" || current === "legacy_unresolved") return true;
  if (current !== "finite") return false;
  const days = validDays(draft.retention_days);
  return days !== null && days < settings.retention_days;
}
