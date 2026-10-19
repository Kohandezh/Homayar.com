import { Drawer } from "@heroui/react";
import { Eye } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import { formatDate, formatNumber } from "@/lib/format";
import { useMembers, useTranscriptRevisions } from "@/lib/platform-api";
import { EmptyState, ErrorState, LoadingState } from "@/components/platform/Ui";
import { RESULTS_COPY } from "@/components/platform/results-copy";
import type { TranscriptRevision } from "@/types/platform";

/**
 * The revision history of one meeting's transcript (HOM-246): every revision
 * newest first, who made it and when, and the reason of every correction. The
 * rows are read only views into revisions the server keeps forever; the
 * current revision is marked and has no View button.
 */
export default function TranscriptRevisionsDrawer({
  meetingId,
  open,
  onClose,
  onViewRevision,
}: {
  meetingId: string;
  open: boolean;
  onClose: () => void;
  /** Closes the drawer and shows this revision read-only in the transcript tab. */
  onViewRevision: (revisionId: string) => void;
}) {
  const { lang, dir } = useI18n();
  const copy = RESULTS_COPY[lang].revisions;
  const { user } = useAuth();
  // Fetched only while the drawer is open, like the transcript panel's own data.
  const revisions = useTranscriptRevisions(meetingId, { enabled: open });
  const members = useMembers(open);
  const items = revisions.data?.items ?? [];

  /**
   * The row's author: `System` for machine revisions, `You` for the signed-in
   * user, the member's name once the member list has loaded. While the list is
   * loading or has failed, another member's row shows no name at all rather
   * than a wrong "Unknown member".
   */
  function who(revision: TranscriptRevision): string | null {
    if (revision.created_by_user_id === null) return copy.system;
    if (user?.id === revision.created_by_user_id) return copy.you;
    if (!members.isSuccess) return null;
    const member = (members.data?.items ?? []).find((item) => item.user_id === revision.created_by_user_id);
    return member ? member.display_name : copy.unknownMember;
  }

  const number = (value: number) => formatNumber(value, lang);

  return (
    <Drawer.Backdrop isOpen={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <Drawer.Content placement={dir === "rtl" ? "left" : "right"} className="justify-end">
        <Drawer.Dialog aria-label={copy.title} className="w-[28rem] max-w-[90vw] p-4">
          <Drawer.CloseTrigger aria-label={copy.close} />
          <Drawer.Header>
            <Drawer.Heading>{copy.title}</Drawer.Heading>
          </Drawer.Header>
          <Drawer.Body>
            {revisions.isPending ? (
              <LoadingState label={RESULTS_COPY[lang].loading} />
            ) : revisions.isError ? (
              <ErrorState error={revisions.error} fallback={copy.failed} />
            ) : items.length === 0 ? (
              <EmptyState label={copy.empty} />
            ) : (
              <ul className="revision-list">
                {items.map((revision) => {
                  const author = who(revision);
                  return (
                  <li key={revision.id} className="revision-row">
                    <div className="revision-row-head">
                      <strong>{copy.label(number(revision.revision_number))}</strong>
                      {revision.is_current && <span className="revision-current-badge">{copy.current}</span>}
                    </div>
                    <div className="revision-row-meta">
                      {/* An unknown source is shown as sent, never guessed. */}
                      <span>{copy.sources[revision.source] ?? revision.source}</span>
                      {author !== null && <span dir="auto">{author}</span>}
                      <time dateTime={revision.created_at}>{formatDate(revision.created_at, lang)}</time>
                    </div>
                    {revision.source === "correction" && revision.note !== null && (
                      <p className="revision-note" dir="auto">{revision.note}</p>
                    )}
                    {!revision.is_current && (
                      <button type="button" className="platform-button" onClick={() => onViewRevision(revision.id)}>
                        <Eye size={14} aria-hidden="true" />
                        {copy.view(number(revision.revision_number))}
                      </button>
                    )}
                  </li>
                  );
                })}
              </ul>
            )}
          </Drawer.Body>
        </Drawer.Dialog>
      </Drawer.Content>
    </Drawer.Backdrop>
  );
}
