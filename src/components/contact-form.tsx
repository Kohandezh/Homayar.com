"use client";

import { useState, type FormEvent } from "react";
import { CheckIcon } from "@/components/icons";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const TEAM_SIZE_OPTIONS = ["1-50", "51-200", "201-1000", "1000+"] as const;

const INPUT_CLASS =
  "w-full rounded-lg border border-line bg-raised px-3.5 py-2.5 text-sm text-ink placeholder:text-ink-faint transition-colors focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/40";

const LABEL_CLASS = "mb-1.5 block text-sm font-medium text-ink-soft";

export interface ContactFormLabels {
  name: string;
  email: string;
  company: string;
  teamSize: string;
  message: string;
  submit: string;
}

type FieldErrors = { name?: string; email?: string };

export function ContactForm({
  labels,
  successMessage,
}: {
  labels: ContactFormLabels;
  successMessage: string;
}) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [teamSize, setTeamSize] = useState("");
  const [message, setMessage] = useState("");

  const [errors, setErrors] = useState<FieldErrors>({});
  const [status, setStatus] = useState<
    "idle" | "submitting" | "success" | "error"
  >("idle");
  const [serverError, setServerError] = useState("");

  function validate(): FieldErrors {
    const next: FieldErrors = {};
    if (!name.trim()) {
      next.name = "Please enter your name.";
    }
    if (!email.trim()) {
      next.email = "Please enter your work email.";
    } else if (!EMAIL_PATTERN.test(email.trim())) {
      next.email = "Please enter a valid email address.";
    }
    return next;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextErrors = validate();
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    setStatus("submitting");
    setServerError("");
    try {
      const res = await fetch("/api/demo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          email: email.trim(),
          company,
          teamSize,
          message,
        }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => null)) as {
          error?: string;
        } | null;
        throw new Error(
          body?.error ??
            "Something went wrong sending your request. Please try again, or email info@ksf.ir.",
        );
      }
      setStatus("success");
    } catch (err) {
      setStatus("error");
      setServerError(
        err instanceof Error && err.message
          ? err.message
          : "Something went wrong sending your request. Please try again, or email info@ksf.ir.",
      );
    }
  }

  if (status === "success") {
    return (
      <div
        role="status"
        className="card-surface flex flex-col items-start gap-4 rounded-2xl p-8"
      >
        <span className="flex h-12 w-12 items-center justify-center rounded-full border border-accent/40 bg-accent/10 text-accent">
          <CheckIcon className="h-6 w-6" />
        </span>
        <h2 className="font-display text-xl font-semibold tracking-tight text-ink">
          Request received
        </h2>
        <p className="leading-relaxed text-ink-soft">{successMessage}</p>
      </div>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      noValidate
      className="card-surface rounded-2xl p-6 sm:p-8"
    >
      <div className="grid gap-5">
        {status === "error" && serverError && (
          <p
            role="alert"
            className="rounded-lg border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger"
          >
            {serverError}
          </p>
        )}

        <div>
          <label htmlFor="contact-name" className={LABEL_CLASS}>
            {labels.name} <span className="text-accent">*</span>
          </label>
          <input
            id="contact-name"
            name="name"
            type="text"
            autoComplete="name"
            required
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              if (errors.name) setErrors((prev) => ({ ...prev, name: undefined }));
            }}
            aria-invalid={errors.name ? true : undefined}
            aria-describedby={errors.name ? "contact-name-error" : undefined}
            className={`${INPUT_CLASS} ${errors.name ? "border-danger" : ""}`}
          />
          {errors.name && (
            <p id="contact-name-error" className="mt-1.5 text-sm text-danger">
              {errors.name}
            </p>
          )}
        </div>

        <div>
          <label htmlFor="contact-email" className={LABEL_CLASS}>
            {labels.email} <span className="text-accent">*</span>
          </label>
          <input
            id="contact-email"
            name="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              if (errors.email)
                setErrors((prev) => ({ ...prev, email: undefined }));
            }}
            aria-invalid={errors.email ? true : undefined}
            aria-describedby={errors.email ? "contact-email-error" : undefined}
            className={`${INPUT_CLASS} ${errors.email ? "border-danger" : ""}`}
          />
          {errors.email && (
            <p id="contact-email-error" className="mt-1.5 text-sm text-danger">
              {errors.email}
            </p>
          )}
        </div>

        <div>
          <label htmlFor="contact-company" className={LABEL_CLASS}>
            {labels.company}
          </label>
          <input
            id="contact-company"
            name="company"
            type="text"
            autoComplete="organization"
            value={company}
            onChange={(e) => setCompany(e.target.value)}
            className={INPUT_CLASS}
          />
        </div>

        <div>
          <label htmlFor="contact-team-size" className={LABEL_CLASS}>
            {labels.teamSize}
          </label>
          <select
            id="contact-team-size"
            name="teamSize"
            value={teamSize}
            onChange={(e) => setTeamSize(e.target.value)}
            className={`${INPUT_CLASS} ${teamSize === "" ? "text-ink-muted" : ""}`}
          >
            <option value="">Select a range</option>
            {TEAM_SIZE_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option} agents
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="contact-message" className={LABEL_CLASS}>
            {labels.message}
          </label>
          <textarea
            id="contact-message"
            name="message"
            rows={4}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            className={`${INPUT_CLASS} resize-y`}
          />
        </div>

        <button
          type="submit"
          disabled={status === "submitting"}
          className="inline-flex h-12 items-center justify-center rounded-xl bg-accent px-6 text-base font-semibold text-bg transition-colors hover:bg-accent-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-60"
        >
          {status === "submitting" ? "Sending…" : labels.submit}
        </button>
      </div>
    </form>
  );
}
