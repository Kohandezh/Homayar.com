import { useState, type FormEvent } from "react";
import {
  Alert,
  Button,
  Checkbox,
  CheckboxGroup,
  Description,
  FieldError,
  Input,
  Label,
  Modal,
  Radio,
  RadioGroup,
  TextField,
} from "@heroui/react";

import {
  API_KEY_SCOPES,
  DEFAULT_EXPIRY_DAYS,
  EXPIRY_CHOICES,
  NAME_MAX_LENGTH,
  createPostBody,
} from "@/components/admin/api-keys/api-key-model";
import { ErrorState } from "@/components/platform/Ui";
import type { ErrorCodeMessages } from "@/lib/error-messages";
import { useI18n } from "@/lib/i18n-context";
import { useCreateApiKey } from "@/lib/platform-api";
import { useCan } from "@/lib/use-can";

/** Error codes of POST /api-keys (backend/routers/v1/api_keys.py), as texts (K3). */
const CREATE_ERROR_CODES: ErrorCodeMessages = {
  too_many_api_keys: {
    fa: "این فضای کاری به بیشترین تعداد کلید فعال رسیده است. کلیدی را که لازم ندارید باطل کنید و دوباره تلاش کنید.",
    en: "This workspace already has the most active keys allowed. Revoke a key you no longer use, then try again.",
  },
  scope_grant_denied: {
    fa: "فقط دسترسی‌هایی را می‌توانید به کلید بدهید که خودتان دارید.",
    en: "You can only give a key the access you have yourself.",
  },
  invalid_scopes: {
    fa: "یکی از دسترسی‌های انتخاب‌شده برای کلید API مجاز نیست.",
    en: "One of the chosen scopes is not allowed for API keys.",
  },
  invalid_expiry: {
    fa: "مدت انقضای کوتاه‌تری انتخاب کنید.",
    en: "Choose a shorter expiry.",
  },
};

/** The draft of the create form; it always starts empty, with 90 days (K3). */
interface CreateDraft {
  name: string;
  scopes: string[];
  days: number;
}

function emptyDraft(): CreateDraft {
  return { name: "", scopes: [], days: DEFAULT_EXPIRY_DAYS };
}

/**
 * The create modal (K3) and, after a 201, the created view that shows the raw
 * key exactly once (K5). The raw key lives only in this component's state:
 * never in a query, the URL, storage or a toast, and it is dropped on every
 * close path (Done, Esc, the close button, and any close of the form view).
 */
export default function CreateApiKeyModal() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const can = useCan();
  const createMutation = useCreateApiKey();
  const [isOpen, setOpen] = useState(false);
  const [draft, setDraft] = useState<CreateDraft>(emptyDraft);
  // Validation only speaks after a submit attempt, so an untouched form shows
  // no error and a reopened form starts clean.
  const [submitted, setSubmitted] = useState(false);
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);

  const pending = createMutation.isPending;
  const nameInvalid = submitted && draft.name.trim().length === 0;
  const scopesInvalid = submitted && draft.scopes.length === 0;
  const view = createdKey === null ? "form" : "created";

  /** Every close path runs this, so reopening always shows an empty form. */
  function resetAll() {
    setDraft(emptyDraft());
    setSubmitted(false);
    setCreatedKey(null);
    setCopied(false);
    setCopyFailed(false);
    createMutation.reset();
  }

  function changeOpen(open: boolean) {
    // A close while the POST runs would lose its answer, so the modal waits.
    if (!open && pending) return;
    if (!open) resetAll();
    setOpen(open);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setSubmitted(true);
    if (draft.name.trim().length === 0 || draft.scopes.length === 0) return;
    setCopyFailed(false);
    createMutation.mutate(createPostBody(draft.name, draft.scopes, draft.days), {
      // Only the raw key moves into local state; the mutation itself is
      // uncached (gcTime 0) and the list is invalidated, nothing more.
      onSuccess: (created) => setCreatedKey(created.key),
    });
  }

  function copyKey() {
    setCopyFailed(false);
    // The clipboard can be missing or refuse (insecure origin, permission);
    // either way the key stays selectable next to the button.
    const write = navigator.clipboard?.writeText?.bind(navigator.clipboard);
    if (!write) {
      setCopyFailed(true);
      return;
    }
    Promise.resolve(write(createdKey ?? ""))
      .then(() => setCopied(true))
      .catch(() => setCopyFailed(true));
  }

  return (
    <Modal isOpen={isOpen} onOpenChange={changeOpen}>
      <Button variant="primary">{isFa ? "ساخت کلید API" : "Create API key"}</Button>
      <Modal.Backdrop isDismissable={view === "form"}>
        <Modal.Container>
          <Modal.Dialog>
            {view === "form" ? (
              // A flex column, like the dialog itself: the body flexes and
              // scrolls, and the footer stays on screen, instead of the whole
              // form growing past the clipped dialog.
              <form onSubmit={submit} noValidate className="flex min-h-0 flex-1 flex-col">
                <Modal.Header>
                  <Modal.Heading>{isFa ? "ساخت کلید API" : "Create API key"}</Modal.Heading>
                </Modal.Header>
                {/* The body scrolls on short screens: the dialog is clipped to
                    the viewport (scroll="inside"), so without this the expiry
                    choices would be unreachable there. */}
                <Modal.Body className="space-y-4 overflow-y-auto">
                  <TextField
                    variant="secondary"
                    value={draft.name}
                    onChange={(name) => setDraft((current) => ({ ...current, name }))}
                    isRequired
                    isInvalid={nameInvalid}
                    isReadOnly={pending}
                    fullWidth
                  >
                    <Label>{isFa ? "نام" : "Name"}</Label>
                    <Input dir="auto" maxLength={NAME_MAX_LENGTH} autoComplete="off" />
                    <Description>
                      {isFa ? "برای نمونه: PBX دفتر مرکزی" : "For example: Main office PBX"}
                    </Description>
                    <FieldError>{isFa ? "یک نام وارد کنید" : "Enter a name"}</FieldError>
                  </TextField>

                  <CheckboxGroup
                    variant="secondary"
                    value={draft.scopes}
                    onChange={(scopes) => setDraft((current) => ({ ...current, scopes }))}
                    isInvalid={scopesInvalid}
                    isReadOnly={pending}
                  >
                    <Label>{isFa ? "دسترسی‌ها" : "Scopes"}</Label>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      {API_KEY_SCOPES.map(({ scope, en, fa }) => {
                        const allowed = can.has(scope);
                        return (
                          <Checkbox key={scope} value={scope} isDisabled={!allowed}>
                            <Checkbox.Content>
                              <Checkbox.Control>
                                <Checkbox.Indicator />
                              </Checkbox.Control>
                              {lang === "fa" ? fa : en}
                            </Checkbox.Content>
                            <Description>
                              <span dir="ltr" className="font-mono">{scope}</span>
                              {!allowed && (
                                <>
                                  {" — "}
                                  {isFa ? "خودتان این دسترسی را ندارید" : "You do not have this access yourself"}
                                </>
                              )}
                            </Description>
                          </Checkbox>
                        );
                      })}
                    </div>
                    <FieldError>
                      {isFa ? "دست‌کم یک دسترسی انتخاب کنید" : "Choose at least one scope"}
                    </FieldError>
                  </CheckboxGroup>

                  <RadioGroup
                    variant="secondary"
                    value={String(draft.days)}
                    onChange={(value) =>
                      setDraft((current) => ({ ...current, days: Number(value) }))
                    }
                    isReadOnly={pending}
                  >
                    <Label>{isFa ? "انقضا پس از" : "Expires after"}</Label>
                    {EXPIRY_CHOICES.map((choice) => (
                      <Radio key={choice.days} value={String(choice.days)}>
                        <Radio.Content>
                          <Radio.Control>
                            <Radio.Indicator />
                          </Radio.Control>
                          {lang === "fa" ? choice.fa : choice.en}
                        </Radio.Content>
                      </Radio>
                    ))}
                  </RadioGroup>

                  {createMutation.error && (
                    <ErrorState
                      error={createMutation.error}
                      codes={CREATE_ERROR_CODES}
                      fallback={isFa ? "ساخت کلید ناموفق بود." : "Could not create the API key."}
                    />
                  )}
                </Modal.Body>
                <Modal.Footer>
                  <Button variant="ghost" isDisabled={pending} onPress={() => changeOpen(false)}>
                    {isFa ? "انصراف" : "Cancel"}
                  </Button>
                  <Button type="submit" variant="primary" isDisabled={pending} isPending={pending}>
                    {pending ? (isFa ? "در حال ساخت" : "Creating") : (isFa ? "ساخت کلید" : "Create key")}
                  </Button>
                </Modal.Footer>
              </form>
            ) : (
              <>
                <Modal.Header>
                  <Modal.Heading>
                    {isFa ? "کلید API ساخته شد" : "API key created"}
                  </Modal.Heading>
                </Modal.Header>
                <Modal.Body className="space-y-4 overflow-y-auto">
                  <Alert status="warning">
                    <Alert.Indicator aria-hidden="true" />
                    <Alert.Content>
                      <Alert.Description>
                        {isFa
                          ? "این کلید را همین حالا کپی کنید و جای امنی نگه دارید. برای امنیت شما، فقط یک بار نمایش داده می‌شود."
                          : "Copy this key now and keep it somewhere safe. For your security we show it only once."}
                      </Alert.Description>
                    </Alert.Content>
                  </Alert>

                  <TextField
                    variant="secondary"
                    value={createdKey ?? ""}
                    isReadOnly
                    fullWidth
                  >
                    <Label>{isFa ? "کلید API جدید شما" : "Your new API key"}</Label>
                    <Input dir="ltr" readOnly autoComplete="off" />
                  </TextField>

                  {copyFailed && (
                    <p className="m-0 platform-form-error" role="alert">
                      {isFa
                        ? "کپی نشد. کلید را انتخاب کنید و خودتان کپی کنید."
                        : "Copy failed. Select the key and copy it by hand."}
                    </p>
                  )}
                </Modal.Body>
                <Modal.Footer>
                  <Button variant="secondary" onPress={copyKey}>
                    {copied ? (isFa ? "کپی شد" : "Copied") : (isFa ? "کپی کلید" : "Copy key")}
                  </Button>
                  <Button variant="primary" onPress={() => changeOpen(false)}>
                    {isFa ? "تمام" : "Done"}
                  </Button>
                </Modal.Footer>
              </>
            )}
          </Modal.Dialog>
        </Modal.Container>
      </Modal.Backdrop>
    </Modal>
  );
}
