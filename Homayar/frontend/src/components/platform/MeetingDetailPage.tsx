import { useEffect, useRef, useState, type AudioHTMLAttributes, type FormEvent, type RefObject } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Disclosure } from "@heroui/react";
import { ArrowLeft, Check, Download, Pencil, Trash2, X } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import { API_BASE_URL } from "@/lib/api";
import { formatDate, formatDuration, languageName } from "@/lib/format";
import { apiErrorInfo } from "@/lib/api-errors";
import { shortcutTargetBlocked } from "@/lib/keyboard";
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
  usePlayback,
  useUpdateMeetingTitle,
} from "@/lib/platform-api";
import { ErrorState, LoadingState, PageHeader, StatusBadge } from "@/components/platform/Ui";
import CallReport from "@/components/platform/CallReport";
import MeetingDataPanel from "@/components/platform/MeetingDataPanel";
import MeetingResults, { type ResultsOpenRequest } from "@/components/platform/MeetingResults";
import PlaybackShortcuts from "@/components/platform/PlaybackShortcuts";
import ProcessingStatus from "@/components/platform/ProcessingStatus";
import { PROCESSING_COPY } from "@/components/platform/processing-copy";
import { REPORT_COPY } from "@/components/platform/report-copy";
import type { ResultsTab } from "@/components/platform/results-copy";
import type { CallReportResponse, ReportCompleteness } from "@/types/platform";

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
    loadingRecording: "Loading the recording",
    recordingRetention: "This recording is no longer available. The workspace retention period has passed.",
    recordingDeleting: "This recording is being deleted and can no longer be played.",
    recordingStatus: "Recording and transcript status",
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
    loadingRecording: "در حال دریافت فایل صوتی",
    recordingRetention: "این فایل صوتی دیگر در دسترس نیست. دورهٔ نگهداری فضای کاری گذشته است.",
    recordingDeleting: "این فایل صوتی در حال حذف است و دیگر قابل پخش نیست.",
    recordingStatus: "وضعیت فایل صوتی و متن",
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
 *
 * HOM-247: the element's source is the playback descriptor's `media_url`,
 * resolved against the API origin. The descriptor decides whether an audio
 * element exists at all: 404/410 answers replace it with their own wording.
 */
/** What the playback descriptor allows the `Recording` section to show (HOM-247). */
type PlayerState = "loading" | "ready" | "retention" | "deleting" | "unavailable";

function RecordingPlayer({
  state,
  src,
  completeness,
  atMs,
  lang,
  audioRef,
  onFailed,
  audioEvents,
}: {
  state: PlayerState;
  /** The descriptor's `media_url`, resolved against the API origin; null until it loads. */
  src: string | null;
  completeness: ReportCompleteness | null;
  atMs: number | null;
  lang: "fa" | "en";
  /** Owned by the page, so the call report's evidence buttons can seek and play. */
  audioRef: RefObject<HTMLAudioElement | null>;
  /** The element could not load the media: the page stops offering play buttons. */
  onFailed: () => void;
  /** The page's listeners, which keep the transcript in step with the audio. */
  audioEvents: Pick<AudioHTMLAttributes<HTMLAudioElement>, "onPlay" | "onPause" | "onSeeked" | "onTimeUpdate">;
}) {
  const copy = COPY[lang];

  // The element mounts only once the descriptor loads, so the cue waits for
  // `src` too: a `?at=` link still lands on its time.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || atMs === null) return;
    const cue = () => {
      audio.currentTime = atMs / 1000;
    };
    if (audio.readyState >= 1) cue();
    else audio.addEventListener("loadedmetadata", cue, { once: true });
    return () => audio.removeEventListener("loadedmetadata", cue);
  }, [atMs, src, state, audioRef]);

  const statusText = {
    loading: copy.loadingRecording,
    retention: copy.recordingRetention,
    deleting: copy.recordingDeleting,
    unavailable: copy.playerUnavailable,
    ready: null,
  }[state];

  return (
    <section className="meeting-player platform-surface" aria-label={copy.player}>
      {statusText !== null || src === null ? (
        <p className="m-0 text-sm text-text-secondary" role="status">{statusText ?? copy.playerUnavailable}</p>
      ) : (
        <audio
          ref={audioRef}
          controls
          preload={atMs === null ? "none" : "metadata"}
          src={src}
          onError={onFailed}
          {...audioEvents}
          aria-label={copy.player}
        />
      )}
      <p className="m-0 text-xs text-text-muted">{copy.playerNote}</p>
      {completeness?.is_complete === false && (
        <ul className="player-notices" aria-label={copy.recordingStatus}>
          {completeness.notices.map((code) => (
            <li key={code}>{REPORT_COPY[lang].notices[code] ?? code}</li>
          ))}
        </ul>
      )}
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
  // HOM-247: the playback descriptor, asked only when a recording can play.
  const playback = usePlayback(meeting.data?.recording && workspace ? meetingId : null);
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
    if (!first) {
      void queryClient.invalidateQueries({ queryKey: platformQueryKeys.report(meetingId) });
      // The player's completeness notices follow the status too (HOM-247).
      void queryClient.invalidateQueries({ queryKey: platformQueryKeys.playback(meetingId) });
    }
  }, [status, meetingId, queryClient]);

  // HOM-247: the recording's source comes from the playback descriptor. A
  // media error is kept per source, so another meeting starts clean.
  const mediaSrc = playback.data
    ? new URL(playback.data.media_url, new URL(API_BASE_URL, window.location.origin)).toString()
    : null;
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const audioFailed = mediaSrc !== null && failedSrc === mediaSrc;
  // 404 and 410 mean the recording is gone, even after a first good answer.
  // Any other error ends playback only when nothing was loaded: a failed
  // background refetch must not take away a player that works.
  const playbackStatus = playback.isError ? apiErrorInfo(playback.error).status : null;
  const playerState =
    playbackStatus === 410 ? "retention"
    : playbackStatus === 404 ? "deleting"
    : (playback.isError && !playback.data) || audioFailed ? "unavailable"
    : mediaSrc === null ? "loading"
    : "ready";

  // Where the audio is, in milliseconds. It stays null until the first play
  // or seek, so no transcript turn is marked before the person listens, and
  // it belongs to one source, so another meeting starts unmarked.
  const [position, setPosition] = useState<{ src: string; ms: number } | null>(null);
  const playbackMs = position !== null && position.src === mediaSrc ? position.ms : null;
  /** `start`: a play or a seek may begin tracking; time updates only continue it. */
  function notePosition(start: boolean) {
    const audio = audioRef.current;
    if (!audio || mediaSrc === null) return;
    const ms = audio.currentTime * 1000;
    setPosition((previous) => (start || previous?.src === mediaSrc ? { src: mediaSrc, ms } : previous));
  }
  const audioEvents = {
    onPlay: () => notePosition(true),
    onSeeked: () => notePosition(true),
    onPause: () => notePosition(false),
    onTimeUpdate: () => notePosition(false),
  };

  /** The audio's live position, read at the moment a key needs it (N and P). */
  function positionMs(): number | null {
    const audio = audioRef.current;
    return audio ? audio.currentTime * 1000 : null;
  }

  /** Seeks without playing (N and P in the transcript) and without touching ?at=. */
  function seekTo(ms: number) {
    const audio = audioRef.current;
    if (audio) audio.currentTime = ms / 1000;
  }

  // K, J and L are page keys (HOM-247). They match the physical key, so a
  // Persian layout works too, and they do nothing while a field or a dialog
  // has focus, or when no player is on screen.
  const durationMs = playback.data?.duration_ms ?? null;
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.code !== "KeyK" && event.code !== "KeyJ" && event.code !== "KeyL") return;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (shortcutTargetBlocked(event.target)) return;
      const audio = audioRef.current;
      if (!audio) return;
      event.preventDefault();
      if (event.code === "KeyK") {
        if (audio.paused) void audio.play()?.catch(() => undefined);
        else audio.pause();
        return;
      }
      const delta = event.code === "KeyJ" ? -5 : 5;
      const duration = Number.isFinite(audio.duration) && audio.duration > 0
        ? audio.duration
        : durationMs !== null ? durationMs / 1000 : Infinity;
      audio.currentTime = Math.min(Math.max(audio.currentTime + delta, 0), duration);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [durationMs]);

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
  // HOM-247: play buttons exist only while the audio element is on screen.
  const canPlay = Boolean(current.recording && workspace) && playerState === "ready";
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

  /** One entry point for every "play from here": evidence buttons and transcript turns (HOM-247). */
  function playFrom(startMs: number) {
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
          {/* While the descriptor loads it is not known whether the player holds this time: say nothing more yet. */}
          {canPlay ? ` ${copy.atPlayer}` : playerState === "loading" && current.recording && workspace ? "" : ` ${copy.noRecording}`}
        </p>
      )}
      {current.recording && workspace && (
        <>
          <RecordingPlayer
            key={`${workspace.id}:${meetingId}`}
            state={playerState}
            src={mediaSrc}
            completeness={playback.data?.completeness ?? null}
            atMs={atMs}
            lang={lang}
            audioRef={audioRef}
            onFailed={() => setFailedSrc(mediaSrc)}
            audioEvents={audioEvents}
          />
          {canPlay && <PlaybackShortcuts lang={lang} />}
        </>
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
          onPlay={playFrom}
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
                {hasResults && (
                  <MeetingResults
                    meeting={current}
                    atMs={navigatedAt}
                    open={openRequest}
                    canPlay={canPlay}
                    playbackMs={playbackMs}
                    onPlayFrom={playFrom}
                    onSeek={seekTo}
                    getPositionMs={positionMs}
                  />
                )}
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
