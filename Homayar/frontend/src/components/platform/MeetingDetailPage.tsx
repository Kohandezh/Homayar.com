import { useEffect, useRef, useState, type FormEvent, type RefObject } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Disclosure } from "@heroui/react";
import { ArrowLeft, Check, Download, Pencil, Trash2, X } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import { API_BASE_URL } from "@/lib/api";
import { formatDate, formatDuration, languageName } from "@/lib/format";
import { apiErrorInfo } from "@/lib/api-errors";
import { useCan } from "@/lib/use-can";
import {
  forgetMeetingContent,
  platformQueryKeys,
  saveDownload,
  useDeleteMeeting,
  useDownloadRecording,
  useMeeting,
  useMeetingReport,
  useMeetingSummary,
  useUpdateMeetingTitle,
} from "@/lib/platform-api";
import { ErrorState, LoadingState, PageHeader, StatusBadge } from "@/components/platform/Ui";
import CallReport from "@/components/platform/CallReport";
import MeetingDataPanel from "@/components/platform/MeetingDataPanel";
import MeetingResults, { type ResultsOpenRequest } from "@/components/platform/MeetingResults";
import ProcessingStatus from "@/components/platform/ProcessingStatus";
import { PROCESSING_COPY } from "@/components/platform/processing-copy";
import { REPORT_COPY } from "@/components/platform/report-copy";
import type { ResultsTab } from "@/components/platform/results-copy";
import type { CallReportResponse } from "@/types/platform";

const COPY = {
  en: {
    loading: "Loading meeting",
    failed: "The meeting could not be loaded.",
    back: "Back to meetings",
    download: "Download recording",
    rename: "Rename meeting",
    delete: "Delete meeting",
    confirmDelete: "Delete this meeting? Its recording, transcript and results are removed for everyone in the workspace.",
    saveTitle: "Save title",
    cancel: "Cancel",
    titleLabel: "Meeting title",
    status: "Status",
    duration: "Duration",
    language: "Language",
    source: "Source",
    operationFailed: "The requested operation failed.",
    player: "Recording",
    playerNote: "Streamed from the server; access is checked again on every request. The audio is not masked.",
    playerUnavailable: "The recording cannot be played (it may have been deleted, or your access or session changed).",
    noRecording: "This meeting has no playable recording.",
    atInvalid: "The requested time is not valid for this meeting; the meeting opens from the start.",
    atOpened: (time: string) => `Opened at ${time}.`,
    atPlayer: "The player is set to this time (press play to listen).",
  },
  fa: {
    loading: "در حال دریافت جلسه",
    failed: "جلسه دریافت نشد.",
    back: "بازگشت به جلسه‌ها",
    download: "دریافت فایل صوتی",
    rename: "تغییر عنوان جلسه",
    delete: "حذف جلسه",
    confirmDelete: "این جلسه حذف شود؟ فایل صوتی، متن و نتایج آن برای همهٔ اعضای فضای کاری حذف می‌شود.",
    saveTitle: "ذخیرهٔ عنوان",
    cancel: "انصراف",
    titleLabel: "عنوان جلسه",
    status: "وضعیت",
    duration: "مدت",
    language: "زبان",
    source: "منبع",
    operationFailed: "عملیات درخواست‌شده انجام نشد.",
    player: "فایل صوتی",
    playerNote: "از سرور پخش می‌شود و در هر درخواست دسترسی دوباره بررسی می‌شود. فایل صوتی پوشانده نمی‌شود.",
    playerUnavailable: "فایل صوتی پخش نشد (ممکن است حذف شده باشد یا دسترسی یا نشست شما تغییر کرده باشد).",
    noRecording: "این جلسه فایل صوتی قابل پخش ندارد.",
    atInvalid: "زمان درخواستی برای این جلسه معتبر نیست؛ جلسه از ابتدا باز شد.",
    atOpened: (time: string) => `جلسه در زمان ${time} باز شد.`,
    atPlayer: "پخش‌کننده روی همین زمان قرار گرفته است (برای شنیدن، پخش را بزنید).",
  },
} as const;

/** Meeting sources the API reports; others are shown as sent. */
const SOURCE_LABELS: Record<string, { fa: string; en: string }> = {
  upload: { fa: "بارگذاری فایل", en: "File upload" },
  file_upload: { fa: "بارگذاری فایل", en: "File upload" },
  api: { fa: "API", en: "API" },
};

/**
 * `?at=<milliseconds>` from a search result: a bounded, non-negative integer
 * inside the meeting (one second of slack for rounding); anything else is
 * ignored and reported, never guessed.
 */
function parseAt(raw: string | null, durationSeconds: number): { atMs: number | null; invalid: boolean } {
  if (raw === null) return { atMs: null, invalid: false };
  if (!/^\d{1,9}$/.test(raw)) return { atMs: null, invalid: true };
  const value = Number(raw);
  if (value > Math.max(0, durationSeconds) * 1000 + 1000) return { atMs: null, invalid: true };
  return { atMs: value, invalid: false };
}

/** A body that is not a call report (a proxy page, a wrong route) shows the error state, never a crash. */
function isCallReport(value: unknown): value is CallReportResponse {
  const report = value as Partial<CallReportResponse> | null | undefined;
  return Boolean(report && report.report_version === 1 && report.qa && report.outcome && report.completeness && Array.isArray(report.review_actions));
}

/**
 * Inline playback through the workspace-scoped route (gap G11): the session
 * cookie authenticates and the path names the workspace, so a native
 * <audio> element (which cannot send X-Workspace-ID) plays the right
 * workspace's recording, and every Range request is authorized again.
 */
function RecordingPlayer({
  workspaceId,
  meetingId,
  atMs,
  lang,
  audioRef,
}: {
  workspaceId: string;
  meetingId: string;
  atMs: number | null;
  lang: "fa" | "en";
  /** Owned by the page, so the call report's evidence buttons can seek and play. */
  audioRef: RefObject<HTMLAudioElement | null>;
}) {
  const copy = COPY[lang];
  const [failed, setFailed] = useState(false);
  const src = `${API_BASE_URL}/workspaces/${encodeURIComponent(workspaceId)}/meetings/${encodeURIComponent(meetingId)}/recording`;

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || atMs === null) return;
    const cue = () => {
      audio.currentTime = atMs / 1000;
    };
    if (audio.readyState >= 1) cue();
    else audio.addEventListener("loadedmetadata", cue, { once: true });
    return () => audio.removeEventListener("loadedmetadata", cue);
  }, [atMs, src, audioRef]);

  return (
    <section className="meeting-player platform-surface" aria-label={copy.player}>
      {failed ? (
        <p className="m-0 text-sm text-text-secondary" role="status">{copy.playerUnavailable}</p>
      ) : (
        <audio
          ref={audioRef}
          controls
          preload={atMs === null ? "none" : "metadata"}
          src={src}
          onError={() => setFailed(true)}
          aria-label={copy.player}
        />
      )}
      <p className="m-0 text-xs text-text-muted">{copy.playerNote}</p>
    </section>
  );
}

export default function MeetingDetailPage() {
  const { meetingId = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const { workspace } = useAuth();
  const { lang } = useI18n();
  const copy = COPY[lang];
  const can = useCan();
  const [searchParams] = useSearchParams();
  const [editingTitle, setEditingTitle] = useState(false);
  const [title, setTitle] = useState("");
  const audioRef = useRef<HTMLAudioElement>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [openRequest, setOpenRequest] = useState<ResultsOpenRequest | null>(null);
  const [openedForAt, setOpenedForAt] = useState<number | null>(null);
  // The last evidence start played on this mount. Kept in memory, not in the
  // history entry, so a reload or Back to the page opens the transcript again.
  const [playedAt, setPlayedAt] = useState<number | null>(null);

  const meeting = useMeeting(meetingId);
  // Asked once the meeting is known to exist, so a deleted one costs no extra request.
  const report = useMeetingReport(meeting.data ? meetingId : null);
  const reportData = isCallReport(report.data) ? report.data : null;
  // The report has no analysis input view; the summary says whether the
  // analysis read the masked transcript (gap G10). Asked only when it matters.
  const summary = useMeetingSummary(reportData?.privacy_enforced && reportData.summary ? meetingId : null);
  const reportCopy = REPORT_COPY[lang];
  const processingCopy = PROCESSING_COPY[lang];
  const meetingStatus = meeting.isError ? apiErrorInfo(meeting.error).status : null;
  const meetingGone = meetingStatus === 404 || meetingStatus === 410;
  const uploadReplay = Boolean((location.state as { uploadReplay?: unknown } | null)?.uploadReplay);
  const updateTitle = useUpdateMeetingTitle();
  const deleteMeeting = useDeleteMeeting();
  const downloadRecording = useDownloadRecording();

  // A deleted, tombstoned or inaccessible meeting leaves nothing behind.
  useEffect(() => {
    if (meetingGone) forgetMeetingContent(queryClient, meetingId);
  }, [meetingGone, meetingId, queryClient]);

  // The meeting query polls while it processes; the report follows its status.
  const status = meeting.data?.status;
  const seenStatus = useRef(status);
  useEffect(() => {
    if (status === undefined || seenStatus.current === status) return;
    const first = seenStatus.current === undefined;
    seenStatus.current = status;
    if (!first) void queryClient.invalidateQueries({ queryKey: platformQueryKeys.report(meetingId) });
  }, [status, meetingId, queryClient]);

  if (meeting.isLoading) return <LoadingState label={copy.loading} />;
  if (meetingGone) {
    return (
      <section className="platform-page meeting-detail-page">
        <div className="platform-state platform-error-state" role="alert">
          <span>{processingCopy.deleted}</span>
        </div>
        <Link to="/meetings">{processingCopy.backToMeetings}</Link>
      </section>
    );
  }
  if (meeting.isError) return <ErrorState error={meeting.error} fallback={copy.failed} />;
  if (!meeting.data) return null;
  const current = meeting.data;
  const hasResults = current.status === "completed" || current.status === "partial";
  const { atMs, invalid: atInvalid } = parseAt(searchParams.get("at"), current.duration_seconds);
  const canHold = can.has("legal_hold:manage");
  const canPlay = Boolean(current.recording && workspace);
  // An evidence play button sets `?at=` too, but only a navigation to `?at=`
  // (a pasted link, a search result, a reload, Back) opens the transcript.
  const fromPlay = atMs !== null && atMs === playedAt;
  const navigatedAt = fromPlay ? null : atMs;
  if (navigatedAt !== null && navigatedAt !== openedForAt && hasResults) {
    setOpenedForAt(navigatedAt);
    setAdvancedOpen(true);
  }

  function toggleAdvanced(open: boolean) {
    setAdvancedOpen(open);
    // Reopening with the trigger starts fresh, not on the last requested tab.
    if (!open) setOpenRequest(null);
  }

  function openPanel(panel: ResultsTab, segmentId: string | null = null) {
    setAdvancedOpen(true);
    setOpenRequest((previous) => ({ panel, segmentId, seq: (previous?.seq ?? 0) + 1 }));
  }

  function playEvidence(startMs: number) {
    const audio = audioRef.current;
    if (audio) {
      audio.currentTime = startMs / 1000;
      // A refused play (autoplay rules) still leaves the player cued.
      void audio.play()?.catch(() => undefined);
    }
    setPlayedAt(startMs);
    navigate({ pathname: location.pathname, search: `?at=${startMs}` }, { replace: true });
  }

  function saveTitle(event: FormEvent) {
    event.preventDefault();
    const value = title.trim();
    if (!value) return;
    updateTitle.mutate({ meetingId, title: value }, { onSuccess: () => setEditingTitle(false) });
  }

  function removeMeeting() {
    if (!window.confirm(copy.confirmDelete)) return;
    deleteMeeting.mutate(meetingId, { onSuccess: () => navigate("/meetings") });
  }

  const operationError = updateTitle.error ?? deleteMeeting.error ?? downloadRecording.error;

  return (
    <section className="platform-page meeting-detail-page">
      <PageHeader
        eyebrow={formatDate(current.created_at, lang)}
        title={current.title}
        actions={
          <>
            <Link className="platform-icon-button" to="/meetings" title={copy.back} aria-label={copy.back}>
              <ArrowLeft size={16} aria-hidden="true" className="rtl-mirror" />
            </Link>
            {current.recording && (
              <button
                type="button"
                className="platform-icon-button"
                onClick={() => downloadRecording.mutate(meetingId, { onSuccess: saveDownload })}
                title={copy.download}
                aria-label={copy.download}
                disabled={downloadRecording.isPending}
              >
                <Download size={16} aria-hidden="true" />
              </button>
            )}
            {can.updateMeeting && (
              <button
                type="button"
                className="platform-icon-button"
                onClick={() => { setTitle(current.title); setEditingTitle(true); }}
                title={copy.rename}
                aria-label={copy.rename}
              >
                <Pencil size={15} aria-hidden="true" />
              </button>
            )}
            {can.deleteMeeting && (
              <button
                type="button"
                className="platform-icon-button platform-button-danger"
                onClick={removeMeeting}
                title={copy.delete}
                aria-label={copy.delete}
                disabled={deleteMeeting.isPending}
              >
                <Trash2 size={15} aria-hidden="true" />
              </button>
            )}
          </>
        }
      />

      {editingTitle && (
        <form className="meeting-title-editor" onSubmit={saveTitle}>
          <input className="platform-input" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={240} aria-label={copy.titleLabel} autoFocus />
          <button className="platform-icon-button platform-button-primary" aria-label={copy.saveTitle}><Check size={15} aria-hidden="true" /></button>
          <button type="button" className="platform-icon-button" onClick={() => setEditingTitle(false)} aria-label={copy.cancel}><X size={15} aria-hidden="true" /></button>
        </form>
      )}

      <div className="meeting-facts">
        <div><span>{copy.status}</span><StatusBadge status={current.status} /></div>
        <div><span>{copy.duration}</span><strong><bdi dir="ltr">{formatDuration(current.duration_seconds, lang)}</bdi></strong></div>
        <div><span>{copy.language}</span><strong>{languageName(current.language, lang)}</strong></div>
        <div><span>{copy.source}</span><strong>{SOURCE_LABELS[current.source]?.[lang] ?? current.source}</strong></div>
      </div>

      {atInvalid && <p className="meeting-stage-runs-note" role="status">{copy.atInvalid}</p>}
      {atMs !== null && (
        <p className="meeting-stage-runs-note" role="status">
          {copy.atOpened(formatDuration(atMs / 1000, lang))}
          {` ${current.recording && workspace ? copy.atPlayer : copy.noRecording}`}
        </p>
      )}
      {current.recording && workspace && (
        <RecordingPlayer
          key={`${workspace.id}:${meetingId}`}
          workspaceId={workspace.id}
          meetingId={meetingId}
          atMs={atMs}
          lang={lang}
          audioRef={audioRef}
        />
      )}

      {report.isLoading ? (
        <LoadingState label={reportCopy.loading} />
      ) : report.isError || (report.data !== undefined && !reportData) ? (
        <ErrorState error={report.error} fallback={reportCopy.failed} />
      ) : reportData ? (
        <CallReport
          report={reportData}
          canPlay={canPlay}
          panelsAvailable={hasResults}
          analysisOnRedacted={
            reportData.privacy_enforced && reportData.summary !== null && summary.data?.analysis_run?.input_view === "redacted"
          }
          onPlay={playEvidence}
          onShowSegment={(segmentId) => openPanel("transcript", segmentId)}
          onOpenPanel={(panel) => openPanel(panel)}
        />
      ) : null}

      {uploadReplay && <p className="meeting-stage-runs-note" role="status">{processingCopy.uploadReplay}</p>}
      <ProcessingStatus meeting={current} canReplay={can.manageWorkspace} />

      {(hasResults || canHold) && (
        <Disclosure className="meeting-advanced" isExpanded={advancedOpen} onExpandedChange={toggleAdvanced}>
          <Disclosure.Heading>
            <Disclosure.Trigger className="platform-button meeting-advanced-trigger">
              {reportCopy.advanced}
              <Disclosure.Indicator />
            </Disclosure.Trigger>
          </Disclosure.Heading>
          <Disclosure.Content>
            {/* Mounted only while open: nothing below is fetched or shown before the user asks. */}
            {advancedOpen && (
              <div className="meeting-advanced-body">
                {hasResults && <MeetingResults meeting={current} atMs={navigatedAt} open={openRequest} />}
                {canHold && <MeetingDataPanel meetingId={meetingId} />}
              </div>
            )}
          </Disclosure.Content>
        </Disclosure>
      )}

      {operationError && <ErrorState error={operationError} fallback={copy.operationFailed} />}
    </section>
  );
}
