import { RotateCcw } from "lucide-react";
import { apiErrorInfo } from "@/lib/api-errors";
import { useI18n } from "@/lib/i18n-context";
import { formatNumber } from "@/lib/format";
import { useReplayJob } from "@/lib/platform-api";
import { NONTERMINAL_MEETING_STATUSES } from "@/lib/status-labels";
import type { MeetingDetail, StageRun } from "@/types/platform";
import { StatusBadge } from "@/components/platform/Ui";
import { PROCESSING_COPY, STAGE_ORDER, type ProcessingCopy } from "@/components/platform/processing-copy";

function stageName(stage: string, copy: ProcessingCopy) {
  return copy.stages[stage] ?? stage;
}

function StageRow({ stage, run, copy }: { stage: string; run: StageRun | undefined; copy: ProcessingCopy }) {
  const reason = run?.reason_code ? copy.reasons[run.reason_code] : null;
  return (
    <li>
      <span>{stageName(stage, copy)}</span>
      {run ? <StatusBadge status={run.state} /> : <span className="processing-not-started">{copy.notStarted}</span>}
      {run?.reason_code && (
        <small>{reason ?? <bdi dir="ltr">{run.reason_message ?? run.reason_code}</bdi>}</small>
      )}
      {run?.is_mock && <small className="meeting-stage-mock">{copy.mock}</small>}
    </li>
  );
}

/**
 * The processing state exactly as the API reports it: meeting status, job
 * state, attempts and one row per pipeline stage. There is no progress
 * percentage in the contract, so none is shown.
 */
export default function ProcessingStatus({ meeting, canReplay }: { meeting: MeetingDetail; canReplay: boolean }) {
  const { lang } = useI18n();
  const copy = PROCESSING_COPY[lang];
  const replay = useReplayJob();
  const job = meeting.latest_job;
  const active = (NONTERMINAL_MEETING_STATUSES as readonly string[]).includes(meeting.status);
  const runs = new Map((job?.stages ?? []).map((run) => [run.stage, run]));
  const stageList = active
    ? [...STAGE_ORDER, ...[...runs.keys()].filter((stage) => !(STAGE_ORDER as readonly string[]).includes(stage))]
    : [...runs.keys()];
  const retryScheduled = active && job?.stage === "retry_scheduled";
  const time = (value: string) =>
    new Intl.DateTimeFormat(lang === "fa" ? "fa-IR" : "en", { timeStyle: "short" }).format(new Date(value));
  const replayCode = replay.isError ? apiErrorInfo(replay.error).code : null;

  if (!job && !active && meeting.status !== "cancelled") return null;

  return (
    <section className="processing-status" aria-labelledby="processing-heading">
      <div className="processing-headline" role="status" aria-live="polite">
        <h2 id="processing-heading">{copy.heading}</h2>
        <StatusBadge status={meeting.status} />
        {job && job.attempt_count > 1 && (
          <span className="processing-attempt">
            {copy.attempt(formatNumber(job.attempt_count, lang), formatNumber(job.max_attempts, lang))}
          </span>
        )}
      </div>

      {active && (
        <div className="meeting-processing-state">
          <span className="meeting-processing-indicator" aria-hidden="true" />
          <div>
            {retryScheduled ? (
              <strong>
                {job?.failure_code && <>{copy.lastAttemptFailed(job.failure_code)} </>}
                {job?.retry_after ? copy.retryScheduled(time(job.retry_after)) : null}
              </strong>
            ) : job?.state === "running" && copy.stages[job.stage] ? (
              <strong>{copy.running(stageName(job.stage, copy))}</strong>
            ) : (
              <strong>{copy.waiting}</strong>
            )}
            <span>{copy.serverSide}</span>
          </div>
        </div>
      )}

      {meeting.status === "partial" && <p className="meeting-stage-runs-note">{copy.partial}</p>}
      {meeting.status === "cancelled" && <p className="meeting-stage-runs-note">{copy.cancelled}</p>}

      {stageList.length > 0 && (
        <ol className="meeting-stage-runs processing-stage-list">
          {stageList.map((stage) => (
            <StageRow key={stage} stage={stage} run={runs.get(stage)} copy={copy} />
          ))}
        </ol>
      )}

      {job?.state === "failed" && (
        <div className="meeting-job-error" role="alert">
          <strong>{copy.failedTitle}</strong>
          {job.failure_code && <bdi dir="ltr">{job.failure_code}</bdi>}
          {job.failure_class === "permanent" && <span>{copy.failedPermanent}</span>}
        </div>
      )}

      {job?.state === "dead_letter" && (
        <div className="meeting-job-error" role="alert">
          <strong>{copy.deadLetterTitle}</strong>
          <span>
            {copy.deadLetterBody}
            {job.failure_code && <> <bdi dir="ltr">({job.failure_code})</bdi></>}
          </span>
          {canReplay ? (
            <div className="processing-replay">
              <button
                type="button"
                className="platform-button"
                onClick={() => replay.mutate({ jobId: job.id, meetingId: meeting.id })}
                disabled={replay.isPending || replay.isSuccess}
              >
                <RotateCcw size={15} aria-hidden="true" />
                {copy.replay}
              </button>
              <small>{copy.replayHint}</small>
            </div>
          ) : (
            <span>{copy.askAdmin}</span>
          )}
        </div>
      )}
      {replay.isSuccess && <p className="meeting-stage-runs-note" role="status">{copy.replayQueued}</p>}
      {replay.isError && (
        <p className="processing-replay-error" role="alert">
          {(replayCode && copy.replayErrors[replayCode]) || copy.replayFailed}
        </p>
      )}
    </section>
  );
}
