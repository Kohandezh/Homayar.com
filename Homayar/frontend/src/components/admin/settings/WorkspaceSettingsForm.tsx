import { useState, type FormEvent } from "react";
import { RotateCcw, Save, Trash2 } from "lucide-react";
import {
  AlertDialog,
  Button,
  Checkbox,
  CheckboxGroup,
  Description,
  FieldError,
  Input,
  Label,
  RadioGroup,
  TextField,
} from "@heroui/react";

import {
  NAME_MAX_LENGTH,
  PII_REDACTION_CLASSES,
  buildSettingsPatch,
  canonicalClasses,
  draftFrom,
  retentionShrinks,
  validateDraft,
  type SettingsDraft,
} from "@/components/admin/settings/settings-model";
import { OptionRadio, SettingSwitch, SettingsSection } from "@/components/admin/settings/SettingsControls";
import { formatDate, parseWholeNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import type {
  DuplicateMediaPolicy,
  PiiRedactionClass,
  RecordScope,
  UpdateWorkspaceSettingsShape,
  WorkspaceSettings,
} from "@/types/platform";

const PII_CLASS_LABELS: Record<PiiRedactionClass, { fa: string; en: string }> = {
  email: { fa: "ایمیل", en: "Email" },
  phone: { fa: "شماره تلفن", en: "Phone" },
  ir_national_id: { fa: "کد ملی", en: "National ID" },
  bank_card: { fa: "کارت بانکی", en: "Bank card" },
  iban: { fa: "شماره شبا", en: "IBAN" },
  ir_postal_code: { fa: "کد پستی", en: "Postal code" },
};

/** The order the checkboxes are shown in; the patch always uses the server order. */
const PII_CLASS_DISPLAY_ORDER: PiiRedactionClass[] = [
  "email",
  "phone",
  "ir_national_id",
  "bank_card",
  "iban",
  "ir_postal_code",
];

/**
 * Compares drafts with the class list as a set and the days by their number,
 * so "30" and "۳۰" match after a language switch.
 */
function draftKey(draft: SettingsDraft): string {
  return JSON.stringify({
    ...draft,
    retention_days: parseWholeNumber(draft.retention_days) ?? draft.retention_days,
    pii_redaction_classes: canonicalClasses(draft.pii_redaction_classes),
  });
}

function isPiiClass(value: string): value is PiiRedactionClass {
  return (PII_REDACTION_CLASSES as readonly string[]).includes(value);
}

/**
 * The editable settings. The draft lives here; the parent remounts the form
 * with the saved values after each save, so the draft starts from the server.
 */
export default function WorkspaceSettingsForm({
  settings,
  isPending,
  canEditPii,
  onSave,
}: {
  settings: WorkspaceSettings;
  isPending: boolean;
  canEditPii: boolean;
  onSave: (patch: Partial<UpdateWorkspaceSettingsShape>) => void;
}) {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const [draft, setDraft] = useState<SettingsDraft>(() => draftFrom(settings, lang));
  const [confirmingShrink, setConfirmingShrink] = useState(false);

  const invalid = validateDraft(draft);
  const patch = buildSettingsPatch(settings, draft, { canEditPii });
  // Any edit can be discarded, also one the patch leaves out (invalid days text).
  const isDirty = draftKey(draft) !== draftKey(draftFrom(settings, lang));
  const hasChanges = Object.keys(patch).length > 0;
  const canSave = !isPending && hasChanges && invalid.length === 0;
  const legacyUnresolved = settings.retention_mode === "legacy_unresolved";
  const piiLocked = !canEditPii;

  function update<K extends keyof SettingsDraft>(key: K, value: SettingsDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSave) return;
    // Shorter retention deletes data for good, so it is confirmed first.
    if (retentionShrinks(settings, draft)) {
      setConfirmingShrink(true);
      return;
    }
    onSave(patch);
  }

  function confirmShrink() {
    setConfirmingShrink(false);
    if (canSave) onSave(patch);
  }

  return (
    <form className="grid gap-4" onSubmit={submit} noValidate>
      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
        <SettingsSection title={isFa ? "عمومی" : "General"}>
          <TextField
            variant="secondary"
            value={draft.name}
            onChange={(value) => update("name", value)}
            isRequired
            isInvalid={invalid.includes("name")}
            isReadOnly={isPending}
            fullWidth
          >
            <Label>{isFa ? "نام فضای کاری" : "Workspace name"}</Label>
            <Input dir="auto" maxLength={NAME_MAX_LENGTH} autoComplete="organization" />
            <Description>
              {isFa
                ? "این نام در سربرگ و فهرست فضاهای کاری نمایش داده می‌شود."
                : "Shown in the header and in the workspace list."}
            </Description>
            <FieldError>{isFa ? "نام فضای کاری را وارد کنید." : "Enter a workspace name."}</FieldError>
          </TextField>
        </SettingsSection>

        <SettingsSection title={isFa ? "نگهداری داده" : "Data retention"}>
          <RadioGroup
            variant="secondary"
            value={draft.retention_mode || null}
            onChange={(value) => update("retention_mode", value as SettingsDraft["retention_mode"])}
            isReadOnly={isPending}
            aria-describedby={legacyUnresolved ? "retention-legacy" : undefined}
          >
            <Label>{isFa ? "سیاست نگهداری داده" : "Data retention policy"}</Label>
            <Description>
              {isFa
                ? "حذف بر اساس زمان فقط با سیاست محدود یا فوری انجام می‌شود؛ حذف دستی همیشه در دسترس است."
                : "Age-based deletion only happens under the finite or immediate policy; explicit deletion is always available."}
            </Description>
            <OptionRadio
              value="finite"
              label={isFa ? "محدود: حذف پس از تعداد روز مشخص" : "Finite: delete after a set number of days"}
              help={isFa ? "داده‌ها چند روز پس از بارگذاری حذف می‌شوند." : "Data is deleted a set number of days after upload."}
            />
            <OptionRadio
              value="indefinite"
              label={isFa ? "نامحدود: فقط حذف دستی" : "Indefinite: only explicit deletion"}
              help={isFa ? "داده‌ها فقط با درخواست صریح حذف می‌شوند." : "Data is deleted only on explicit request."}
            />
            <OptionRadio
              value="immediate"
              label={isFa ? "فوری: حذف پس از پایان پردازش" : "Immediate: delete once processing finishes"}
              help={isFa ? "داده‌ها پس از پایان پردازش حذف می‌شوند." : "Data is deleted once processing finishes."}
            />
          </RadioGroup>
          {legacyUnresolved && (
            <p id="retention-legacy" className="platform-form-error m-0" role="alert">
              {isFa
                ? "مقدار قدیمی «۰ روز» مبهم است و تا انتخاب یک سیاست صریح هیچ داده‌ای بر اساس زمان حذف نمی‌شود."
                : "The old \"0 days\" value is ambiguous; nothing is deleted by age until you choose an explicit policy."}
            </p>
          )}

          {draft.retention_mode === "finite" && (
            // A text field, not a number field: the raw text (Persian digits
            // included) must reach the validation unchanged.
            <TextField
              variant="secondary"
              value={draft.retention_days}
              onChange={(value) => update("retention_days", value)}
              isRequired
              isInvalid={invalid.includes("retention_days")}
              isReadOnly={isPending}
            >
              <Label>{isFa ? "مدت نگهداری (روز)" : "Retention period (days)"}</Label>
              <Input type="text" inputMode="numeric" autoComplete="off" className="max-w-40" />
              <Description>{isFa ? "از ۱ تا ۳۶۵۰ روز پس از بارگذاری." : "1 to 3650 days after upload."}</Description>
              <FieldError>
                {isFa ? "یک عدد صحیح بین ۱ تا ۳۶۵۰ وارد کنید." : "Enter a whole number from 1 to 3650."}
              </FieldError>
            </TextField>
          )}

          <SettingSwitch
            label={isFa ? "حذف فایل صوتی پس از پردازش" : "Delete audio after processing"}
            help={
              isFa
                ? "پس از پایان موفق پردازش، فایل ضبط‌شده حذف می‌شود."
                : "The recording file is deleted after processing finishes successfully."
            }
            isSelected={draft.delete_audio_after_processing}
            isReadOnly={isPending}
            onChange={(value) => update("delete_audio_after_processing", value)}
          />
        </SettingsSection>

        <SettingsSection title={isFa ? "اشتراک‌گذاری" : "Sharing"}>
          <SettingSwitch
            label={isFa ? "پیوندهای اشتراک عمومی" : "Public share links"}
            help={
              isFa
                ? "پیوندهایی که هر کسی با داشتن آن‌ها می‌تواند باز کند. خاموش کردن، پیوندهای عمومی موجود را هم از کار می‌اندازد."
                : "Allows share links that anyone with the link can open. Turning it off also stops existing public links from opening."
            }
            isSelected={draft.public_share_links_enabled}
            isReadOnly={isPending}
            onChange={(value) => update("public_share_links_enabled", value)}
          />
          <SettingSwitch
            label={isFa ? "پیوندهای اشتراک سازمانی" : "Organization share links"}
            help={
              isFa
                ? "پیوندهایی برای اعضای سازمانی که این فضای کاری به آن تعلق دارد. خاموش کردن، پیوندهای موجود را هم از کار می‌اندازد."
                : "Allows share links for members of the organization this workspace belongs to. Turning it off also stops existing ones."
            }
            isSelected={draft.organization_share_links_enabled}
            isReadOnly={isPending}
            onChange={(value) => update("organization_share_links_enabled", value)}
          />
        </SettingsSection>

        <SettingsSection title={isFa ? "دامنهٔ دسترسی" : "Access scope"}>
          <RadioGroup
            variant="secondary"
            value={draft.record_scope}
            onChange={(value) => update("record_scope", value as RecordScope)}
            isReadOnly={isPending}
          >
            <Label>{isFa ? "دیدن رکوردها" : "Record visibility"}</Label>
            <OptionRadio
              value="workspace"
              label={isFa ? "کل فضای کاری" : "Whole workspace"}
              help={isFa ? "هر عضو همهٔ رکوردهای فضای کاری را می‌بیند." : "Every member sees every record of the workspace."}
            />
            <OptionRadio
              value="team"
              label={isFa ? "بر اساس تیم" : "By team"}
              help={
                isFa
                  ? "سرپرست‌ها رکوردهای تیم‌های خود را می‌بینند و کارشناس‌ها فقط رکوردهای خودشان را. مالک و مدیرها همیشه همه چیز را می‌بینند."
                  : "Supervisors see their teams' records and agents see only their own. Owners and admins always see everything."
              }
            />
          </RadioGroup>
        </SettingsSection>

        <SettingsSection title={isFa ? "فایل‌های تکراری" : "Duplicates"}>
          <RadioGroup
            variant="secondary"
            value={draft.duplicate_media_policy}
            onChange={(value) => update("duplicate_media_policy", value as DuplicateMediaPolicy)}
            isReadOnly={isPending}
          >
            <Label>{isFa ? "رفتار با فایل تکراری" : "Duplicate files"}</Label>
            <OptionRadio
              value="allow"
              label={isFa ? "پذیرفتن" : "Allow"}
              help={
                isFa
                  ? "فایلی که قبلاً بارگذاری شده دوباره پذیرفته و پردازش می‌شود (و دوباره هزینه دارد)."
                  : "A file that was already uploaded is accepted and processed again (and billed again)."
              }
            />
            <OptionRadio
              value="reject"
              label={isFa ? "رد کردن" : "Reject"}
              help={
                isFa
                  ? "فایلی که قبلاً بارگذاری شده رد می‌شود و چیزی ساخته نمی‌شود."
                  : "A file that was already uploaded is refused and nothing is created."
              }
            />
          </RadioGroup>
        </SettingsSection>

        <SettingsSection title={isFa ? "حریم خصوصی" : "Privacy"}>
          {piiLocked && (
            <p className="m-0 text-sm text-text-secondary">
              {isFa
                ? "فقط مالک فضای کاری می‌تواند این تنظیمات را تغییر دهد."
                : "Only the workspace owner can change these settings."}
            </p>
          )}
          <SettingSwitch
            label={isFa ? "پنهان‌سازی اطلاعات شخصی" : "PII redaction"}
            help={
              isFa
                ? "اطلاعات شخصیِ نوع‌های انتخاب‌شده در متن پیاده‌شده و نتایج پنهان می‌شود."
                : "Personal data of the chosen types is masked in transcripts and results."
            }
            isSelected={draft.pii_redaction_enabled}
            isDisabled={piiLocked}
            isReadOnly={isPending}
            onChange={(value) => update("pii_redaction_enabled", value)}
          />
          <CheckboxGroup
            variant="secondary"
            value={draft.pii_redaction_classes}
            onChange={(values) => update("pii_redaction_classes", values.filter(isPiiClass))}
            // Turning redaction off keeps the chosen types; they only stop being editable.
            isDisabled={piiLocked || !draft.pii_redaction_enabled}
            isReadOnly={isPending}
            isInvalid={invalid.includes("pii_redaction_classes")}
          >
            <Label>{isFa ? "نوع داده‌های پنهان‌شده" : "Redacted data types"}</Label>
            <Description>{isFa ? "کدام نوع اطلاعات شخصی پنهان شود." : "Which kinds of personal data are masked."}</Description>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {PII_CLASS_DISPLAY_ORDER.map((value) => (
                <Checkbox key={value} value={value}>
                  <Checkbox.Content>
                    <Checkbox.Control>
                      <Checkbox.Indicator />
                    </Checkbox.Control>
                    {PII_CLASS_LABELS[value][lang]}
                  </Checkbox.Content>
                </Checkbox>
              ))}
            </div>
            <FieldError>{isFa ? "دست‌کم یک نوع داده را انتخاب کنید." : "Choose at least one data type."}</FieldError>
          </CheckboxGroup>
        </SettingsSection>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border-subtle pt-4">
        <span className="text-xs text-text-muted">
          {isFa ? "آخرین تغییر:" : "Last updated:"} {formatDate(settings.updated_at, lang)}
        </span>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="ghost"
            isDisabled={isPending || !isDirty}
            onPress={() => setDraft(draftFrom(settings, lang))}
          >
            <RotateCcw aria-hidden="true" size={15} />
            {isFa ? "کنار گذاشتن تغییرات" : "Discard changes"}
          </Button>
          <Button type="submit" variant="primary" isDisabled={!canSave}>
            <Save aria-hidden="true" size={15} />
            {isPending ? (isFa ? "در حال ذخیره" : "Saving") : (isFa ? "ذخیره تنظیمات" : "Save settings")}
          </Button>
        </div>
      </div>

      <AlertDialog isOpen={confirmingShrink} onOpenChange={setConfirmingShrink}>
        <AlertDialog.Backdrop>
          <AlertDialog.Container>
            <AlertDialog.Dialog>
              <AlertDialog.Header>
                <AlertDialog.Icon status="danger" />
                <AlertDialog.Heading>
                  {isFa ? "داده‌های قدیمی‌تر حذف شود؟" : "Delete older data?"}
                </AlertDialog.Heading>
              </AlertDialog.Header>
              <AlertDialog.Body>
                <p className="m-0 text-sm text-muted">
                  {isFa
                    ? "با سیاست نگهداری جدید، داده‌هایی که از محدودهٔ تازه قدیمی‌ترند برای همیشه حذف می‌شوند. این کار برگشت‌پذیر نیست."
                    : "Under the new retention policy, older data will be permanently deleted. This cannot be undone."}
                </p>
              </AlertDialog.Body>
              <AlertDialog.Footer>
                <Button variant="ghost" onPress={() => setConfirmingShrink(false)}>
                  {isFa ? "انصراف" : "Cancel"}
                </Button>
                <Button variant="danger" onPress={confirmShrink}>
                  <Trash2 aria-hidden="true" size={14} />
                  {isFa ? "حذف داده‌ها و ذخیره" : "Delete data and save"}
                </Button>
              </AlertDialog.Footer>
            </AlertDialog.Dialog>
          </AlertDialog.Container>
        </AlertDialog.Backdrop>
      </AlertDialog>
    </form>
  );
}
