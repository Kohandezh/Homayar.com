"use client";

import { useId, useRef, useState, type FormEvent } from "react";
import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { Icon } from "@/components/ui/Icon";

type Copy = Dictionary["contact"]["form"];
type FieldKey = "name" | "email" | "company" | "deployment";
type Status = "idle" | "sending" | "sent" | "failed";

const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function ContactForm({ copy, locale }: { copy: Copy; locale: Locale }) {
  const [errors, setErrors] = useState<Partial<Record<FieldKey, string>>>({});
  const [status, setStatus] = useState<Status>("idle");
  const summaryRef = useRef<HTMLDivElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const uid = useId();
  const id = (k: string) => `${uid}-${k}`;

  function validate(data: FormData) {
    const next: Partial<Record<FieldKey, string>> = {};
    const name = String(data.get("name") ?? "").trim();
    const email = String(data.get("email") ?? "").trim();
    const company = String(data.get("company") ?? "").trim();
    const deployment = String(data.get("deployment") ?? "");
    if (name.length < 2) next.name = copy.errors.name;
    if (!emailPattern.test(email)) next.email = copy.errors.email;
    if (!company) next.company = copy.errors.company;
    if (!deployment) next.deployment = copy.errors.deployment;
    return next;
  }

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = new FormData(form);
    const next = validate(data);
    setErrors(next);
    if (Object.keys(next).length > 0) {
      // Focus the summary so keyboard and screen-reader users land on the list of problems.
      requestAnimationFrame(() => summaryRef.current?.focus());
      return;
    }
    setStatus("sending");
    try {
      const res = await fetch("/api/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: data.get("name"),
          email: data.get("email"),
          company: data.get("company"),
          role: data.get("role") ?? "",
          deployment: data.get("deployment"),
          volume: data.get("volume") ?? "",
          message: data.get("message") ?? "",
          locale,
        }),
      });
      if (res.status !== 202) throw new Error("bad_status");
      setStatus("sent");
      form.reset();
    } catch {
      setStatus("failed");
    }
  }

  const errorEntries = (Object.keys(errors) as FieldKey[]).filter((k) => errors[k]);

  if (status === "sent") {
    return (
      <div role="status" className="flex flex-col items-start gap-3 rounded-2xl bg-primary-soft p-6">
        <span className="inline-grid size-11 place-items-center rounded-full bg-primary text-white">
          <Icon name="check" size={22} strokeWidth={2.5} />
        </span>
        <h2 className="text-xl font-bold">{copy.successTitle}</h2>
        <p className="text-ink-2">{copy.successText}</p>
      </div>
    );
  }

  return (
    <form ref={formRef} onSubmit={onSubmit} noValidate className="flex flex-col gap-5" data-testid="contact-form">
      {errorEntries.length > 0 ? (
        <div
          ref={summaryRef}
          tabIndex={-1}
          role="alert"
          aria-labelledby={id("summary-title")}
          className="rounded-xl border border-danger/40 bg-[#fdecec] p-4"
        >
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
        <div role="alert" className="rounded-xl border border-danger/40 bg-[#fdecec] p-4">
          <p className="font-semibold text-danger">{copy.failedTitle}</p>
          <p className="text-sm text-ink-2">{copy.failedText}</p>
        </div>
      ) : null}

      <div className="grid gap-5 sm:grid-cols-2">
        <Field id={id("name")} label={copy.name} required={copy.required} error={errors.name}>
          <input id={id("name")} name="name" type="text" autoComplete="name" className="field-input" aria-invalid={errors.name ? "true" : undefined} aria-describedby={errors.name ? `${id("name")}-error` : undefined} />
        </Field>
        <Field id={id("email")} label={copy.email} required={copy.required} error={errors.email}>
          <input id={id("email")} name="email" type="email" inputMode="email" autoComplete="email" dir="ltr" className="field-input" aria-invalid={errors.email ? "true" : undefined} aria-describedby={errors.email ? `${id("email")}-error` : undefined} />
        </Field>
        <Field id={id("company")} label={copy.company} required={copy.required} error={errors.company}>
          <input id={id("company")} name="company" type="text" autoComplete="organization" className="field-input" aria-invalid={errors.company ? "true" : undefined} aria-describedby={errors.company ? `${id("company")}-error` : undefined} />
        </Field>
        <Field id={id("role")} label={copy.role} optional={copy.optional}>
          <select id={id("role")} name="role" className="field-input" defaultValue="">
            {copy.roleOptions.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </Field>
        <Field id={id("deployment")} label={copy.deployment} required={copy.required} error={errors.deployment}>
          <select id={id("deployment")} name="deployment" className="field-input" defaultValue="" aria-invalid={errors.deployment ? "true" : undefined} aria-describedby={errors.deployment ? `${id("deployment")}-error` : undefined}>
            {copy.deploymentOptions.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </Field>
        <Field id={id("volume")} label={copy.volume} optional={copy.optional}>
          <select id={id("volume")} name="volume" className="field-input" defaultValue="">
            {copy.volumeOptions.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </Field>
      </div>
      <Field id={id("message")} label={copy.message} optional={copy.optional} hint={copy.messageHint}>
        <textarea id={id("message")} name="message" rows={4} className="field-input" aria-describedby={`${id("message")}-hint`} />
      </Field>

      <div className="flex flex-wrap items-center gap-4">
        <button type="submit" className="btn btn-primary min-w-40" disabled={status === "sending"} aria-busy={status === "sending"}>
          {status === "sending" ? copy.sending : copy.submit}
        </button>
        <p className="text-sm text-ink-2">{copy.privacyNote}</p>
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
        <span className="text-xs font-medium text-ink-2">{required ? `${required} *` : optional}</span>
      </label>
      {children}
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-ink-2">
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
