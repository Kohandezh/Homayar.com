import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { FileAudio, RotateCcw, Upload, X } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import { formatAudioSeconds, formatBytes, formatClock } from "@/lib/format";
import { useBillingUsage, useUploadMeeting } from "@/lib/platform-api";
import { useCan } from "@/lib/use-can";
import { classifyUploadFailure, type UploadFailure } from "@/lib/upload-errors";
import { ACCEPTED_EXTENSIONS, UPLOAD_COPY } from "@/components/platform/upload-copy";

function newUploadKey() {
  return `web-${crypto.randomUUID()}`;
}

function useSecondTicker(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return [now, setNow] as const;
}

interface Transfer {
  loaded: number;
  total: number | null;
}

/**
 * Upload with the product's idempotent contract (POST /uploads,
 * Idempotency-Key). One key belongs to one chosen file: a transient failure
 * is retried with the same key so an upload the server already accepted is
 * replayed rather than duplicated, and a refusal of the bytes needs a new
 * file choice. Nothing is resubmitted automatically.
 */
export default function UploadPanel() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { lang } = useI18n();
  const copy = UPLOAD_COPY[lang];
  const can = useCan();
  const verified = Boolean(user?.email_verified || user?.phone_verified);
  const upload = useUploadMeeting();
  const usage = useBillingUsage(can.readBilling && can.createMeeting);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [language, setLanguage] = useState("fa");
  const [uploadKey, setUploadKey] = useState(newUploadKey);
  const [transfer, setTransfer] = useState<Transfer | null>(null);
  const [failure, setFailure] = useState<UploadFailure | null>(null);
  const [retryAt, setRetryAt] = useState<number | null>(null);
  const controller = useRef<AbortController | null>(null);
  const [now, setNow] = useSecondTicker(retryAt !== null);
  const pending = upload.isPending;

  // Leaving the page stops the transfer; the same key makes a later retry safe.
  useEffect(() => () => controller.current?.abort(), []);

  useEffect(() => {
    if (!pending) return;
    function warn(event: BeforeUnloadEvent) {
      event.preventDefault();
    }
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [pending]);

  if (!can.createMeeting) {
    return <div id="upload" className="meeting-upload-restriction" role="status">{copy.readOnly}</div>;
  }
  if (!verified) {
    return (
      <div id="upload" className="meeting-upload-restriction" role="status">
        {copy.verifyFirst} <Link to="/verify-email">{copy.verifyLink}</Link>
      </div>
    );
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const next = event.target.files?.[0] ?? null;
    setFile(next);
    setUploadKey(newUploadKey());
    setFailure(null);
    setRetryAt(null);
    setTransfer(null);
    if (next && !title) setTitle(next.name.replace(/\.[^.]+$/, "").slice(0, 240));
  }

  function start() {
    if (!file || pending) return;
    const abort = new AbortController();
    controller.current = abort;
    setFailure(null);
    setRetryAt(null);
    setTransfer({ loaded: 0, total: file.size });
    upload.mutate(
      {
        file,
        idempotencyKey: uploadKey,
        title: title.trim() || undefined,
        language,
        transfer: {
          signal: abort.signal,
          onUploadProgress: (loaded, total) => setTransfer({ loaded, total: total ?? file.size }),
        },
      },
      {
        onSuccess: (response) => {
          setFile(null);
          navigate(`/meetings/${response.meeting.id}`, {
            state: { uploadReplay: response.idempotent_replay },
          });
        },
        onError: (error) => {
          const next = classifyUploadFailure(error);
          setFailure(next);
          setTransfer(null);
          if (next.retry === "same_key" && next.retryAfterSeconds) {
            const issued = Date.now();
            setNow(issued);
            setRetryAt(issued + next.retryAfterSeconds * 1000);
          }
        },
        onSettled: () => {
          controller.current = null;
        },
      },
    );
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    start();
  }

  const waitSeconds = retryAt === null ? 0 : Math.max(0, Math.ceil((retryAt - now) / 1000));
  const sent = transfer && transfer.total ? Math.min(1, transfer.loaded / transfer.total) : 0;
  const validating = pending && transfer !== null && transfer.total !== null && transfer.loaded >= transfer.total;
  const audio = usage.data?.items.find((item) => item.metric === "audio_seconds");
  const percent = new Intl.NumberFormat(lang === "fa" ? "fa-IR" : "en", { style: "percent" }).format(sent);

  return (
    <section id="upload" className="platform-surface upload-panel" aria-labelledby="upload-panel-title">
      <h2 id="upload-panel-title">{copy.title}</h2>
      <form className="meeting-upload" onSubmit={submit}>
        <label className="meeting-file-picker">
          <FileAudio size={18} aria-hidden="true" />
          <span>{file ? copy.changeFile : copy.chooseFile}</span>
          <input
            type="file"
            accept={ACCEPTED_EXTENSIONS.join(",")}
            onChange={chooseFile}
            disabled={pending}
            aria-describedby="upload-formats"
          />
        </label>
        <label className="upload-field">
          <span>{copy.meetingTitle}</span>
          <input
            className="platform-input"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            maxLength={240}
            disabled={pending}
          />
        </label>
        <label className="upload-field">
          <span>{copy.language}</span>
          <select
            className="platform-select"
            value={language}
            onChange={(event) => setLanguage(event.target.value)}
            disabled={pending}
            aria-describedby="upload-language-hint"
          >
            <option value="fa">فارسی</option>
            <option value="en">English</option>
            <option value="ar">العربية</option>
          </select>
        </label>
        {pending ? (
          <button type="button" className="platform-button" onClick={() => controller.current?.abort()}>
            <X size={15} aria-hidden="true" />
            {copy.cancel}
          </button>
        ) : (
          <button className="platform-button platform-button-primary" disabled={!file || failure?.retry === "new_file"}>
            <Upload size={15} aria-hidden="true" />
            {copy.submit}
          </button>
        )}
      </form>
      <p id="upload-formats" className="upload-hint">
        {copy.formats} <span id="upload-language-hint">{copy.languageHint}</span>
      </p>
      {file && (
        <p className="upload-file-name">
          <bdi dir="ltr">{file.name}</bdi> · {formatBytes(file.size, lang)}
        </p>
      )}

      {can.readBilling && audio && (
        <p className="upload-allowance">
          {audio.limit === null
            ? copy.allowanceUnlimited
            : copy.allowance(
                formatAudioSeconds(audio.remaining ?? 0, lang),
                audio.reserved ? formatAudioSeconds(audio.reserved, lang) : "",
              )}
        </p>
      )}

      <div role="status" aria-live="polite" className="upload-progress">
        {pending && !validating && transfer && (
          <>
            <span>{copy.transferring(percent)}</span>
            <progress max={1} value={sent} aria-label={copy.transferring(percent)} />
            <small>{copy.transferNote}</small>
          </>
        )}
        {validating && <span>{copy.validating}</span>}
      </div>

      {failure && (
        <div className="upload-failure" role="alert">
          <p>{failure.kind === "cancelled" ? copy.cancelled : copy.failures[failure.kind]}</p>
          {failure.kind === "quota" && failure.quota?.remaining !== null && failure.quota?.requested !== null && failure.quota && (
            <p>
              {copy.quotaDetail(
                formatAudioSeconds(failure.quota.remaining ?? 0, lang),
                formatAudioSeconds(failure.quota.requested ?? 0, lang),
              )}
            </p>
          )}
          {failure.kind === "quota" && <a href="/contact">{copy.contactSales}</a>}
          {failure.kind === "verification_required" && <Link to="/verify-email">{copy.verifyLink}</Link>}
          {failure.kind === "duplicate" && failure.meetingId && (
            <Link to={`/meetings/${failure.meetingId}`}>{copy.openExisting}</Link>
          )}
          {failure.retry === "new_file" && <p>{copy.chooseAgain}</p>}
          {failure.retry === "same_key" && file && (
            <div className="upload-retry">
              {waitSeconds > 0 && <span>{copy.retryIn(formatClock(waitSeconds, lang))}</span>}
              <button type="button" className="platform-button" onClick={start} disabled={waitSeconds > 0}>
                <RotateCcw size={15} aria-hidden="true" />
                {copy.retry}
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
