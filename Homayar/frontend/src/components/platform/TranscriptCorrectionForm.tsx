import { useId, useState, type FormEvent } from "react";
import { useI18n } from "@/lib/i18n-context";
import { apiErrorInfo } from "@/lib/api-errors";
import type { TranscriptSegment, TranscriptSpeaker } from "@/types/platform";
import { RESULTS_COPY } from "@/components/platform/results-copy";

/** What one correction sends: only the fields that changed, plus the reason. */
export interface SegmentCorrection {
  reason: string;
  text?: string;
  speaker_key?: string;
}

/** Collapses whitespace runs to one space and trims the edges: the reason rule of the API. */
function collapseReason(value: string): string {
  return value.trim().replaceAll(/\s+/g, " ");
}

/**
 * The inline edit form of one transcript turn (HOM-246): the text, the
 * speaker and the required reason. It is shown for one segment at a time and
 * validates everything the server checks, so Save stays disabled until the
 * correction is well-formed.
 */
export default function TranscriptCorrectionForm({
  segment,
  speakers,
  isPending,
  error,
  onSave,
  onCancel,
  onReload,
}: {
  segment: TranscriptSegment;
  speakers: TranscriptSpeaker[];
  /** The correction request is in flight. */
  isPending: boolean;
  /** The correction request failed; the form keeps what was typed. */
  error: unknown;
  onSave: (correction: SegmentCorrection) => void;
  onCancel: () => void;
  /** Leaves edit mode and refetches the transcript (the revision_stale way out). */
  onReload: () => void;
}) {
  const { lang } = useI18n();
  const copy = RESULTS_COPY[lang].correction;
  const textId = useId();
  const speakerId = useId();
  const reasonId = useId();
  const [text, setText] = useState(segment.text);
  const [speakerKey, setSpeakerKey] = useState(segment.speaker_key);
  const [reason, setReason] = useState("");

  const trimmedText = text.trim();
  const trimmedOriginal = segment.text.trim();
  const collapsedReason = collapseReason(reason);
  const reasonValid = collapsedReason.length >= 3 && collapsedReason.length <= 500;
  // Compare trimmed to trimmed: whitespace around a stored text is not a change.
  const canSave = !isPending && trimmedText.length > 0 && reasonValid
    && (trimmedText !== trimmedOriginal || speakerKey !== segment.speaker_key);

  const errorCode = error === null || error === undefined ? null : apiErrorInfo(error).code;
  const errorText = errorCode !== null && Object.hasOwn(copy.errors, errorCode)
    ? copy.errors[errorCode]
    : copy.failed;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSave) return;
    onSave({
      reason: collapsedReason,
      ...(trimmedText !== trimmedOriginal ? { text: trimmedText } : {}),
      ...(speakerKey !== segment.speaker_key ? { speaker_key: speakerKey } : {}),
    });
  }

  return (
    <form className="segment-edit-form" onSubmit={submit}>
      {/* Explicit htmlFor: a select inside its label would swallow the option
          texts into its accessible name. */}
      <div className="segment-edit-field">
        <label htmlFor={textId}>{copy.turnText}</label>
        <textarea
          id={textId}
          className="platform-input"
          dir="auto"
          value={text}
          onChange={(event) => setText(event.target.value)}
          maxLength={8000}
          rows={3}
          autoFocus
        />
      </div>
      <div className="segment-edit-field">
        <label htmlFor={speakerId}>{copy.speaker}</label>
        <select id={speakerId} value={speakerKey} onChange={(event) => setSpeakerKey(event.target.value)}>
          {speakers.map((speaker) => (
            <option key={speaker.id} value={speaker.speaker_key}>{speaker.display_name ?? speaker.speaker_key}</option>
          ))}
        </select>
      </div>
      <div className="segment-edit-field">
        <label htmlFor={reasonId}>{copy.reasonLabel}</label>
        <input
          id={reasonId}
          className="platform-input"
          dir="auto"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          maxLength={500}
        />
        <small className="segment-edit-hint">{copy.reasonHint}</small>
      </div>
      <div className="segment-edit-actions">
        <button className="platform-button platform-button-primary" disabled={!canSave}>
          {copy.save}
        </button>
        <button type="button" className="platform-button" onClick={onCancel}>
          {RESULTS_COPY[lang].transcript.cancel}
        </button>
      </div>
      {error !== null && error !== undefined && (
        <div className="segment-edit-error" role="alert">
          <p className="m-0">{errorText}</p>
          {errorCode === "revision_stale" && (
            <button type="button" className="platform-button" onClick={onReload}>
              {copy.reloadTranscript}
            </button>
          )}
        </div>
      )}
    </form>
  );
}
