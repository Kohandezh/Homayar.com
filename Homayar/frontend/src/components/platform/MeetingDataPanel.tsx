import { useId, useState } from "react";
import { Button, Table } from "@heroui/react";
import { Lock, Unlock } from "lucide-react";

import OverviewCard from "@/components/admin/overview/OverviewCard";
import { queryCardState } from "@/components/admin/overview/card-state";
import PlaceHoldDialog from "@/components/admin/privacy/PlaceHoldDialog";
import { HOLD_ERROR_CODES, labelOf, PRIVACY_COPY } from "@/components/admin/privacy/privacy-copy";
import { formatReviewDay } from "@/components/admin/privacy/privacy-model";
import ReleaseHoldDialog, { type ReleaseTarget } from "@/components/admin/privacy/ReleaseHoldDialog";
import { formatDay } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useMeetingMedia } from "@/lib/privacy-api";

/**
 * "Data and retention" on the meeting page (HOM-190): the stored recordings
 * of the meeting, their lifecycle, and the legal-hold controls. The page
 * mounts it only for `legal_hold:manage`, so no other role asks for the
 * media list.
 */
export default function MeetingDataPanel({ meetingId }: { meetingId: string }) {
  const { lang } = useI18n();
  const copy = PRIVACY_COPY[lang];
  const query = useMeetingMedia(meetingId);
  const [placing, setPlacing] = useState(false);
  const [releasing, setReleasing] = useState<ReleaseTarget | null>(null);
  const rowIdPrefix = useId();

  const items = query.data?.items ?? [];
  const state = queryCardState(query, (data) => data.items.length === 0);
  // Deleted objects cannot be held; with none left the hold has nothing to keep.
  const hasLive = items.some((item) => item.lifecycle_state !== "deleted");

  return (
    <OverviewCard
      title={copy.panelHeading}
      action={
        <Button variant="secondary" size="sm" isDisabled={!hasLive} onPress={() => setPlacing(true)}>
          <Lock aria-hidden="true" size={15} />
          {copy.placeHold}
        </Button>
      }
      state={state}
      error={query.error}
      errorCodes={HOLD_ERROR_CODES}
      emptyText={copy.noMedia}
    >
      <p className="mt-0 mb-3 text-sm text-muted">{copy.panelIntro}</p>
      <Table className="min-w-0">
        <Table.ScrollContainer>
          <Table.Content aria-label={copy.mediaTable} className="min-w-[720px]">
            <Table.Header>
              <Table.Column isRowHeader>{copy.mediaColumns.kind}</Table.Column>
              <Table.Column>{copy.mediaColumns.state}</Table.Column>
              <Table.Column>{copy.mediaColumns.integrity}</Table.Column>
              <Table.Column>{copy.mediaColumns.retention}</Table.Column>
              <Table.Column>{copy.mediaColumns.hold}</Table.Column>
              <Table.Column aria-label={copy.mediaColumns.actions}>{""}</Table.Column>
            </Table.Header>
            <Table.Body>
              {items.map((item) => {
                const held = item.lifecycle_state === "legal_hold";
                const kindId = `${rowIdPrefix}-${item.id}`;
                return (
                  <Table.Row key={item.id} id={item.id}>
                    <Table.Cell>
                      <span id={kindId}>{labelOf(copy.kinds, item.kind)}</span>
                    </Table.Cell>
                    <Table.Cell>{labelOf(copy.states, item.lifecycle_state)}</Table.Cell>
                    <Table.Cell>{labelOf(copy.integrities, item.integrity)}</Table.Cell>
                    <Table.Cell>
                      {item.retention_expires_at ? (
                        <time dateTime={item.retention_expires_at}>{formatDay(item.retention_expires_at, lang)}</time>
                      ) : (
                        copy.noRetention
                      )}
                    </Table.Cell>
                    <Table.Cell>
                      {held ? (
                        <span className="grid gap-0.5">
                          {/* Free text by a person, in any language: bdi picks its direction. */}
                          {item.legal_hold_reason ? <bdi>{item.legal_hold_reason}</bdi> : <span>—</span>}
                          {item.legal_hold_review_at && (
                            <span className="text-xs text-muted">
                              {copy.reviewOn}:{" "}
                              <time dateTime={item.legal_hold_review_at}>
                                {formatReviewDay(item.legal_hold_review_at, lang)}
                              </time>
                            </span>
                          )}
                        </span>
                      ) : (
                        "—"
                      )}
                    </Table.Cell>
                    <Table.Cell>
                      {held && (
                        <Button
                          variant="secondary"
                          size="sm"
                          // The name stays "Release"; the row's recording kind describes it.
                          aria-describedby={kindId}
                          onPress={() => setReleasing({ mediaId: item.id, meetingId })}
                        >
                          <Unlock aria-hidden="true" size={15} />
                          {copy.release}
                        </Button>
                      )}
                    </Table.Cell>
                  </Table.Row>
                );
              })}
            </Table.Body>
          </Table.Content>
        </Table.ScrollContainer>
      </Table>
      {placing && <PlaceHoldDialog meetingId={meetingId} onClose={() => setPlacing(false)} />}
      {releasing && <ReleaseHoldDialog target={releasing} onClose={() => setReleasing(null)} />}
    </OverviewCard>
  );
}
