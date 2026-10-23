"use client";

import { useId, useRef, useState, type FormEvent } from "react";
import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { contactSchema } from "@/lib/contact-schema";
import { Icon } from "@/components/ui/Icon";
import { Turnstile, type TurnstileHandle } from "@/components/site/Turnstile";

type Copy = Dictionary["contact"]["form"];
type FieldKey = "name" | "email" | "company" | "phone" | "deployment" | "minutes";
// "waiting": the visitor sent the form before the bot check gave a token. It is sent
// as soon as the token arrives.
type Status = "idle" | "waiting" | "sending" | "sent" | "failed";
// A problem with the bot check: the server refused the token, or the script did not load.
type Notice = "turnstileFailed" | "turnstileLoadFailed" | null;

const fieldKeys: FieldKey[] = ["name", "email", "company", "phone", "deployment", "minutes"];

// Persian and Arabic-Indic digits become 0-9, so the stored number is easy to dial.
function latinDigits(value: string) {
  return value.replace(/[۰-۹]/g, (d) => String(d.charCodeAt(0) - 0x06f0)).replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660));
}

function isFieldKey(value: string): value is FieldKey {
  return (fieldKeys as string[]).includes(value);
}

export function ContactForm({ copy, locale }: { copy: Copy; locale: Locale }) {
  const [errors, setErrors] = useState<Partial<Record<FieldKey, string>>>({});
  const [status, setStatus] = useState<Status>("idle");
  const [notice, setNotice] = useState<Notice>(null);
  const [reference, setReference] = useState<string | null>(null);
  // Refs, not state: the token callback submits the form in the same tick it sets them.
  const token = useRef<string | null>(null);
  const submitWhenTokenArrives = useRef(false);
  const turnstileLoadFailed = useRef(false);
  const summaryRef = useRef<HTMLDivElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const turnstileRef = useRef<TurnstileHandle>(null);
  const uid = useId();
  const id = (k: string) => `${uid}-${k}`;

  function toBody(data: FormData) {
    return {
      name: String(data.get("name") ?? ""),
      email: String(data.get("email") ?? ""),
      company: String(data.get("company") ?? ""),
      phone: latinDigits(String(data.get("phone") ?? "")),
      role: String(data.get("role") ?? ""),
      deployment: String(data.get("deployment") ?? ""),
      minutes: String(data.get("minutes") ?? ""),
      message: String(data.get("message") ?? ""),
      locale,
    };
  }

  function errorsFor(fields: string[]) {
    const next: Partial<Record<FieldKey, string>> = {};
    for (const f of fields) if (isFieldKey(f)) next[f] = copy.errors[f];
    return next;
  }

  function showErrors(next: Partial<Record<FieldKey, string>>) {
    setErrors(next);
    requestAnimationFrame(() => summaryRef.current?.focus());
  }

  function onToken(next: string | null) {
    token.current = next;
    if (next && submitWhenTokenArrives.current) {
      submitWhenTokenArrives.current = false;
      formRef.current?.requestSubmit();
    }
  }

  function onTurnstileLoadError() {
    turnstileLoadFailed.current = true;
    submitWhenTokenArrives.current = false;
    setStatus((s) => (s === "waiting" ? "idle" : s));
    setNotice("turnstileLoadFailed");
  }

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (status === "sending") return;
    const form = e.currentTarget;
    const body = toBody(new FormData(form));
    const parsed = contactSchema.safeParse(body);
    if (!parsed.success) {
      const fields = parsed.error.issues.map((i) => String(i.path[0] ?? ""));
      showErrors(errorsFor(fields));
      return;
    }
    setErrors({});
    const currentToken = token.current;
    if (!currentToken) {
      if (turnstileLoadFailed.current) {
        setNotice("turnstileLoadFailed");
        return;
      }
      // A token is single use and is renewed after every submit (also a failed one),
      // so a quick Retry can come before the new token. Send it when the token arrives.
      submitWhenTokenArrives.current = true;
      setNotice(null);
      setStatus("waiting");
      return;
    }
    // This token is now used. The next submit waits for a new one.
    token.current = null;
    setNotice(null);
    setStatus("sending");
    try {
      const res = await fetch("/api/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...parsed.data, turnstileToken: currentToken }),
      });
      if (res.status === 202) {
        const json = (await res.json().catch(() => null)) as { id?: string } | null;
        setReference(typeof json?.id === "string" ? json.id : null);
        setStatus("sent");
        form.reset();
        return;
      }
      if (res.status === 403) {
        const json = (await res.json().catch(() => null)) as { error?: string } | null;
        if (json?.error === "turnstile_failed") {
          setStatus("idle");
          setNotice("turnstileFailed");
          return;
        }
      }
      if (res.status === 400) {
        const json = (await res.json().catch(() => null)) as { fields?: string[] } | null;
        const next = errorsFor(json?.fields ?? []);
        if (Object.keys(next).length > 0) {
          setStatus("idle");
          showErrors(next);
          return;
        }
      }
      setStatus("failed");
    } catch {
      setStatus("failed");
    } finally {
      // A token works only once. Get a new one for the next try.
      turnstileRef.current?.reset();
    }
  }

  const errorEntries = fieldKeys.filter((k) => errors[k]);

  if (status === "sent") {
    return (
      <div role="status" className="flex flex-col items-start gap-3 rounded-2xl border border-accent/40 bg-accent/10 p-6">
        <span className="inline-grid size-11 place-items-center rounded-full bg-accent text-on-accent">
          <Icon name="check" size={22} strokeWidth={2.5} />
        </span>
        <h2 className="text-xl font-bold">{copy.successTitle}</h2>
        <p className="text-ink-2">{copy.successText}</p>
        {reference ? (
          <p className="text-sm text-ink-2">
            {copy.referenceLabel}:{" "}
            <code dir="ltr" className="font-mono font-semibold text-ink" data-testid="contact-reference">
              {reference}
            </code>
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <form ref={formRef} onSubmit={onSubmit} noValidate className="flex flex-col gap-5" data-testid="contact-form">
      {errorEntries.length > 0 ? (
        <div ref={summaryRef} tabIndex={-1} role="alert" aria-labelledby={id("summary-title")} className="rounded-xl border border-danger/50 bg-danger/10 p-4">
          <h2 id={id("summary-title")} className="font-semibold text-danger">
            {copy.errorsTitle}
          </h2>
          <ul className="mt-2 flex list-disc flex-col gap-1 ps-5 text-sm text-danger">
            {errorEntries.map((k) => (
              <li key={k}>
                <a href={`#${id(k)}`} className="underline">
                  {errors[k]}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {status === "failed" ? (
        <div role="alert" className="flex flex-col items-start gap-3 rounded-xl border border-danger/50 bg-danger/10 p-4">
          <div>
            <p className="font-semibold text-danger">{copy.failedTitle}</p>
            <p className="text-sm text-ink-2">{copy.failedText}</p>
          </div>
          <button type="button" className="btn btn-secondary" onClick={() => formRef.current?.requestSubmit()} data-testid="contact-retry">
            <Icon name="rotate" size={16} />
            {copy.retry}
          </button>
        </div>
      ) : null}

      {notice ? (
        <div role="alert" className="rounded-xl border border-danger/50 bg-danger/10 p-4">
          <p className="text-sm font-semibold text-danger">{copy[notice]}</p>
        </div>
      ) : null}

      {status === "waiting" ? (
        <p role="status" className="text-sm text-ink-2">
          {copy.turnstileWaiting}
        </p>
      ) : null}

      <div className="grid gap-5 sm:grid-cols-2">
        <Field id={id("name")} label={copy.name} required={copy.required} error={errors.name}>
          <input id={id("name")} name="name" type="text" autoComplete="name" maxLength={120} className="field-input" aria-invalid={errors.name ? "true" : undefined} aria-describedby={errors.name ? `${id("name")}-error` : undefined} />
        </Field>
        <Field id={id("email")} label={copy.email} required={copy.required} error={errors.email}>
          <input id={id("email")} name="email" type="email" inputMode="email" autoComplete="email" dir="ltr" maxLength={200} className="field-input" aria-invalid={errors.email ? "true" : undefined} aria-describedby={errors.email ? `${id("email")}-error` : undefined} />
        </Field>
        <Field id={id("company")} label={copy.company} required={copy.required} error={errors.company}>
          <input id={id("company")} name="company" type="text" autoComplete="organization" maxLength={160} className="field-input" aria-invalid={errors.company ? "true" : undefined} aria-describedby={errors.company ? `${id("company")}-error` : undefined} />
        </Field>
        <Field id={id("phone")} label={copy.phone} optional={copy.optional} error={errors.phone}>
          <input id={id("phone")} name="phone" type="tel" inputMode="tel" autoComplete="tel" dir="ltr" maxLength={32} className="field-input" aria-invalid={errors.phone ? "true" : undefined} aria-describedby={errors.phone ? `${id("phone")}-error` : undefined} />
        </Field>
        <Field id={id("role")} label={copy.role} optional={copy.optional}>
          <select id={id("role")} name="role" className="field-input" defaultValue="">
            {copy.roleOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </Field>
        <Field id={id("deployment")} label={copy.deployment} required={copy.required} error={errors.deployment}>
          <select id={id("deployment")} name="deployment" className="field-input" defaultValue="" aria-invalid={errors.deployment ? "true" : undefined} aria-describedby={errors.deployment ? `${id("deployment")}-error` : undefined}>
            {copy.deploymentOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </Field>
        <Field id={id("minutes")} label={copy.minutes} optional={copy.optional} error={errors.minutes}>
          <select id={id("minutes")} name="minutes" className="field-input" defaultValue="" aria-invalid={errors.minutes ? "true" : undefined} aria-describedby={errors.minutes ? `${id("minutes")}-error` : undefined}>
            {copy.minutesOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field id={id("message")} label={copy.message} optional={copy.optional} hint={copy.messageHint}>
        <textarea id={id("message")} name="message" rows={4} maxLength={2000} className="field-input" aria-describedby={`${id("message")}-hint`} />
      </Field>

      <Turnstile ref={turnstileRef} locale={locale} onToken={onToken} onLoadError={onTurnstileLoadError} />

      <div className="flex flex-wrap items-center gap-4">
        <button type="submit" className="btn btn-primary min-w-40" disabled={status === "sending" || status === "waiting"} aria-busy={status === "sending" || status === "waiting"}>
          {status === "sending" || status === "waiting" ? copy.sending : copy.submit}
        </button>
        <p className="text-sm text-ink-3">{copy.privacyNote}</p>
      </div>
    </form>
  );
}

function Field({
  id,
  label,
  required,
  optional,
  hint,
  error,
  children,
}: {
  id: string;
  label: string;
  required?: string;
  optional?: string;
  hint?: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="flex items-baseline justify-between gap-2 text-sm font-semibold">
        <span>{label}</span>
        <span className="label text-[11px] font-medium text-ink-3">{required ? `${required} *` : optional}</span>
      </label>
      {children}
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-ink-3">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-error`} className="flex items-center gap-1 text-sm font-medium text-danger">
          <Icon name="alert" size={14} />
          {error}
        </p>
      ) : null}
    </div>
  );
}
