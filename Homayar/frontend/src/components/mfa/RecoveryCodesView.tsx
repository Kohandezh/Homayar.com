import { useId, useState } from "react";
import { useI18n } from "@/lib/i18n-context";
import { MFA_COPY } from "@/components/mfa/mfa-copy";
import { useCopyToClipboard } from "@/components/mfa/copy-to-clipboard";

/**
 * The recovery codes, shown exactly once (HOM-241 AC6): after enrolment and
 * after "Make new recovery codes". The codes live only in the parent's
 * component state (AC8): when this view unmounts they are gone, and nothing
 * but the five MFA API routes ever sees them.
 */
export default function RecoveryCodesView({ codes, onContinue }: { codes: string[]; onContinue: () => void }) {
  const { lang } = useI18n();
  const copy = MFA_COPY[lang].codes;
  const headingId = useId();
  const [saved, setSaved] = useState(false);
  const { copied, failed, copy: copyText } = useCopyToClipboard();

  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} className="mt-0 mb-2 text-xl font-semibold">{copy.heading}</h2>
      <p className="text-sm leading-relaxed text-muted">{copy.body}</p>
      {/* Codes are left-to-right data in both languages (AC9). */}
      <ol dir="ltr" className="m-0 my-4 grid list-none grid-cols-1 gap-1 p-0 font-mono text-sm sm:grid-cols-2">
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ol>
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className="auth-text-button" onClick={() => copyText(codes.join("\n"))}>
          {copied ? copy.copied : copy.copyCodes}
        </button>
        {failed && <p className="m-0 platform-form-error" role="alert">{copy.copyFailed}</p>}
      </div>
      <label className="mt-4 flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={saved}
          onChange={(event) => setSaved(event.target.checked)}
        />
        {copy.savedLabel}
      </label>
      <button type="button" className="auth-submit mt-4" disabled={!saved} onClick={onContinue}>
        {copy.continueLabel}
      </button>
    </section>
  );
}
