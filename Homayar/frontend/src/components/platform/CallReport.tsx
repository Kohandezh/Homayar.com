import { useId, useState, type ReactNode } from "react";
import { Link2, Play } from "lucide-react";
import { useI18n } from "@/lib/i18n-context";
import { formatDuration, formatNumber } from "@/lib/format";
import { statusLabel } from "@/lib/status-labels";
import type { CallReportResponse, ReportEvidenceRef, ReportFinding, ReviewActionKey } from "@/types/platform";
import { StatusBadge } from "@/components/platform/Ui";
import MaskedText from "@/components/platform/MaskedText";
import { QualityPanel } from "@/components/platform/QualityPanel";
import { PROCESSING_COPY } from "@/components/platform/processing-copy";
import { REPORT_COPY, type ReportCopy } from "@/components/platform/report-copy";
import { RESULTS_COPY, type ResultsTab } from "@/components/platform/results-copy";

type Lang = "fa" | "en";

/** The advanced panel each review action opens. */
const ACTION_PANELS: Record<ReviewActionKey, ResultsTab> = {
  correct_transcript: "transcript",
  rename_speaker: "transcript",
  edit_action_items: "actions",
  share: "shares",
  export: "export",
};

export function EvidenceBadge({ status, lang }: { status: string; lang: Lang }) {
  return <StatusBadge status={`evidence-${status}`} label={`${REPORT_COPY[lang].summary.evidence}: ${statusLabel(status, lang)}`} />;
}

function ReportSection({ title, className, children }: { title: string; className?: string; children: ReactNode }) {
  const headingId = useId();
  return (
    <section className={`platform-surface call-report-section ${className ?? ""}`} aria-labelledby={headingId}>
      <h2 id={headingId}>{title}</h2>
      {children}
    </section>
  );
}

function EvidenceList({
  refs,
  canPlay,
  copy,
  lang,
  onPlay,
  onShowSegment,
}: {
  refs: ReportEvidenceRef[];
  canPlay: boolean;
  copy: ReportCopy;
  lang: Lang;
  onPlay: (startMs: number) => void;
  onShowSegment: (segmentId: string) => void;
}) {
  const resultsCopy = RESULTS_COPY[lang];
  return (
    <ul className="call-report-evidence">
      {refs.map((ref) => (
        <li key={ref.id}>
          <q dir="auto"><MaskedText text={ref.text} copy={resultsCopy} revealed={{}} /></q>
          <div className="summary-item-meta">
            <span>{copy.evidence.speaker}: <bdi dir="auto">{ref.speaker_key}</bdi></span>
            <bdi dir="ltr">{`${formatDuration(ref.start_ms / 1000, lang)}–${formatDuration(ref.end_ms / 1000, lang)}`}</bdi>
            {canPlay && (
              <button type="button" className="platform-link-button" onClick={() => onPlay(ref.start_ms)}>
                <Play size={13} aria-hidden="true" />
                {copy.evidence.play(formatDuration(ref.start_ms / 1000, lang))}
              </button>
            )}
            {ref.is_current_revision ? (
              <button type="button" className="platform-link-button" onClick={() => onShowSegment(ref.segment_id)}>
                <Link2 size={13} aria-hidden="true" />
                {copy.evidence.showInTranscript}
              </button>
            ) : (
              <small>{copy.evidence.earlierRevision}</small>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

function findingTypeLabel(finding: ReportFinding, copy: ReportCopy) {
  if (finding.finding_type === "insight" && finding.category) return finding.category;
  return copy.qa.types[finding.finding_type] ?? finding.finding_type;
}

/**
 * The call report of one meeting (WP12-T02): completeness notices, summary,
 * outcome, evidence-backed QA findings and the review actions, in that order.
 * Everything shown comes from GET /meetings/{id}/report; the page owns the
 * player and the advanced panels and is reached through the callbacks.
 */
export default function CallReport({
  report,
  canPlay,
  panelsAvailable = true,
  analysisOnRedacted = false,
  onPlay,
  onShowSegment,
  onOpenPanel,
}: {
  report: CallReportResponse;
  /** Whether the meeting has a playable recording. */
  canPlay: boolean;
  /** Whether the advanced panels exist (completed or partial meetings). */
  panelsAvailable?: boolean;
  /** The summary query says the analysis read the masked transcript (gap G10). */
  analysisOnRedacted?: boolean;
  onPlay: (startMs: number) => void;
  onShowSegment: (segmentId: string) => void;
  onOpenPanel: (panel: ResultsTab) => void;
}) {
  const { lang } = useI18n();
  const copy = REPORT_COPY[lang];
  const resultsCopy = RESULTS_COPY[lang];
  const { summary, outcome, qa, completeness } = report;
  const [waitingAction, setWaitingAction] = useState<ReviewActionKey | null>(null);
  // A finding is listed only with evidence to show, whatever its flag says.
  const supportedFindings = qa.findings.filter((finding) => finding.supported && finding.evidence.length > 0);
  const analysisReason = qa.quality.degradations.find((item) => item.stage === "intelligence")?.reason ?? null;
  const evidence = { canPlay, copy, lang, onPlay, onShowSegment };

  return (
    <div className="call-report">
      {completeness.notices.length > 0 && (
        <ReportSection title={copy.headings.completeness} className="call-report-notices">
          <ul>
            {completeness.notices.map((code) => <li key={code}>{copy.notices[code] ?? code}</li>)}
          </ul>
        </ReportSection>
      )}

      <ReportSection title={copy.headings.summary}>
        {summary ? (
          <>
            {report.view === "redacted" && <p className="content-view-note">{copy.summary.redacted}</p>}
            {report.view === "original" && report.privacy_enforced && <p className="content-view-note">{copy.summary.original}</p>}
            {analysisOnRedacted && <p className="content-view-note">{copy.summary.analysisOnRedacted}</p>}
            <p dir="auto"><MaskedText text={summary.overview} copy={resultsCopy} revealed={{}} /></p>
            <div className="summary-item-meta">
              <EvidenceBadge status={summary.evidence_status} lang={lang} />
              {!summary.supported && <strong className="call-report-unsupported">{copy.summary.notSupported}</strong>}
              {summary.sentiment && <span>{copy.summary.sentiment}: <bdi dir="auto">{summary.sentiment}</bdi></span>}
            </div>
            {summary.topics.length > 0 && (
              <div className="meeting-topics" aria-label={copy.summary.topics}>
                {summary.topics.map((topic) => <span key={topic} dir="auto">{topic}</span>)}
              </div>
            )}
          </>
        ) : (
          <p role="status">
            {copy.summary.noSummary}{" "}
            {analysisReason && copy.summary.unavailableBecause(PROCESSING_COPY[lang].reasons[analysisReason] ?? analysisReason)}
          </p>
        )}
      </ReportSection>

      <ReportSection title={copy.headings.outcome}>
        <p className={`call-report-outcome call-report-outcome-${outcome.state}`}>
          {copy.outcome.states[outcome.state] ?? outcome.state}
        </p>
        {outcome.decisions.length > 0 && (
          <ul className="call-report-items">
            {outcome.decisions.map((decision, index) => (
              <li key={index} data-decision="">
                <p dir="auto"><MaskedText text={decision.text} copy={resultsCopy} revealed={{}} /></p>
                <div className="summary-item-meta">
                  <EvidenceBadge status={decision.evidence_status} lang={lang} />
                  {!decision.supported && <strong className="call-report-unsupported">{copy.outcome.noEvidence}</strong>}
                </div>
                {decision.supported && <EvidenceList refs={decision.evidence} {...evidence} />}
              </li>
            ))}
          </ul>
        )}
      </ReportSection>

      <ReportSection title={copy.headings.qa}>
        <p className="call-report-counts">
          {copy.qa.counts(formatNumber(qa.counts.supported, lang), formatNumber(qa.counts.total, lang))}
        </p>
        <QualityPanel quality={qa.quality} />
        {supportedFindings.length === 0 ? (
          <p role="status">{copy.qa.none}</p>
        ) : (
          <ul className="call-report-items">
            {supportedFindings.map((finding, index) => (
              <li key={`${finding.finding_type}-${finding.finding_id ?? finding.finding_index ?? "x"}-${index}`}>
                <div className="summary-item-meta">
                  <span className="call-report-type" dir="auto">{findingTypeLabel(finding, copy)}</span>
                  <EvidenceBadge status={finding.evidence_status} lang={lang} />
                </div>
                <p dir="auto"><MaskedText text={finding.text} copy={resultsCopy} revealed={{}} /></p>
                <EvidenceList refs={finding.evidence} {...evidence} />
              </li>
            ))}
          </ul>
        )}
      </ReportSection>

      <ReportSection title={copy.headings.reviewActions}>
        <ul className="call-report-actions">
          {report.review_actions.map((item) => (
            <li key={item.action}>
              <button
                type="button"
                className="platform-button"
                disabled={!item.available}
                onClick={() => {
                  if (panelsAvailable) onOpenPanel(ACTION_PANELS[item.action] ?? "transcript");
                  else setWaitingAction(item.action);
                }}
              >
                {copy.reviewActions.labels[item.action] ?? item.action}
              </button>
              {item.available && waitingAction === item.action && (
                <span className="call-report-reason" role="status">{copy.reviewActions.needsResults}</span>
              )}
              {!item.available && item.reason && (
                <span className="call-report-reason">{copy.reviewActions.reasons[item.reason] ?? item.reason}</span>
              )}
            </li>
          ))}
        </ul>
      </ReportSection>
    </div>
  );
}
