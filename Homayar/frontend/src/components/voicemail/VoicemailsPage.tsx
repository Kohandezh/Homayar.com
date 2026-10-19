import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { Button, Chip, Table } from "@heroui/react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import VoicemailDrawer from "@/components/voicemail/VoicemailDrawer";
import VoicemailFilterBar from "@/components/voicemail/VoicemailFilterBar";
import {
  followUpStateLabel,
  voicemailCategoryLabel,
  voicemailPriorityChip,
  voicemailPriorityLabel,
  voicemailStatusLabel,
  VOICEMAIL_COPY,
} from "@/components/voicemail/voicemail-copy";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/components/platform/Ui";
import { formatDate, formatDuration, formatNumber, isolateLtr } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import {
  hasVoicemailFilters,
  readVoicemailFilters,
  voicemailQueryParams,
  writeVoicemailFilters,
  type VoicemailFilters,
} from "@/lib/voicemail-filters";
import { useVoicemails } from "@/lib/voicemail-api";
import type { Voicemail, VoicemailPriority } from "@/types/voicemail";

interface Paging {
  filterKey: string;
  /** One entry per page seen; the first page has no cursor. */
  cursors: Array<string | undefined>;
}

/** The priority chip: urgent in danger, high in warning, normal the default look, low quiet soft. */
function PriorityChip({ priority, lang }: { priority: VoicemailPriority; lang: "fa" | "en" }) {
  const chip = voicemailPriorityChip(priority);
  return (
    <Chip color={chip.color} variant={chip.variant} size="sm">
      <Chip.Label>{voicemailPriorityLabel(priority, lang)}</Chip.Label>
    </Chip>
  );
}

export default function VoicemailsPage() {
  const { lang } = useI18n();
  const copy = VOICEMAIL_COPY[lang];
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => readVoicemailFilters(searchParams), [searchParams]);
  // The open voicemail id lives in the URL, so a deep link opens the drawer.
  // An empty value (a bare "?voicemail=") is no id at all.
  const openId = searchParams.get("voicemail") || null;
  // The cursors of the pages walked so far, kept next to the filters they
  // belong to: other filters describe a different list, where an old cursor
  // means nothing, so paging starts from the first page again.
  const filterKey = useMemo(() => writeVoicemailFilters(filters).toString(), [filters]);
  const [paging, setPaging] = useState<Paging>({ filterKey, cursors: [undefined] });
  const cursors = paging.filterKey === filterKey ? paging.cursors : [undefined];
  const cursor = cursors[cursors.length - 1];

  function goTo(next: Array<string | undefined>) {
    setPaging({ filterKey, cursors: next });
  }

  // React Query hashes the key by value, so a freshly built params object with
  // the same filters and cursor stays one cached query.
  const params = voicemailQueryParams(filters, { before: cursor });
  const voicemailsQuery = useVoicemails(params);
  const items = voicemailsQuery.data?.items ?? [];
  const pageNumber = formatNumber(cursors.length, lang);

  function applyFilters(next: VoicemailFilters) {
    // A new filter is a new list: paging starts over from its first page, and
    // the state must say so at once, or returning to an earlier filter would
    // bring back that filter's old cursor.
    const nextKey = writeVoicemailFilters(next).toString();
    setSearchParams(writeVoicemailFilters(next, searchParams));
    setPaging({ filterKey: nextKey, cursors: [undefined] });
  }

  function openVoicemail(id: string) {
    const next = new URLSearchParams(searchParams);
    next.set("voicemail", id);
    setSearchParams(next);
  }

  function closeVoicemail() {
    const next = new URLSearchParams(searchParams);
    next.delete("voicemail");
    setSearchParams(next);
  }

  /** The row's caller: its number in an LTR isolate, or the unknown text. */
  function callerOf(item: Voicemail) {
    return item.caller_number ? isolateLtr(item.caller_number) : copy.unknownCaller;
  }

  // The page walker stays mounted wherever a page can be walked back from:
  // with rows, and on an emptied later page, where Previous is the only way back.
  const pagingNav = (
    <nav
      className="mt-3 flex items-center justify-between gap-3"
      aria-label={copy.pagingLabel}
    >
      <Button
        variant="secondary"
        size="sm"
        // Still focusable while a page loads, so focus is never dropped to the page.
        aria-disabled={cursors.length === 1 || voicemailsQuery.isFetching}
        onPress={() => {
          if (cursors.length === 1 || voicemailsQuery.isFetching) return;
          goTo(cursors.slice(0, -1));
        }}
      >
        <ChevronLeft size={15} aria-hidden="true" className="rtl-mirror" />
        {copy.previous}
      </Button>
      <span className="text-xs text-muted" aria-live="polite">
        {copy.page(pageNumber)}
      </span>
      {/* No Next once the page was not full: there is no further page to ask for. */}
      {voicemailsQuery.data?.next_before && (
        <Button
          variant="secondary"
          size="sm"
          aria-disabled={voicemailsQuery.isFetching}
          onPress={() => {
            const next = voicemailsQuery.data?.next_before;
            if (!next || voicemailsQuery.isFetching) return;
            goTo([...cursors, next]);
          }}
        >
          {copy.next}
          <ChevronRight size={15} aria-hidden="true" className="rtl-mirror" />
        </Button>
      )}
    </nav>
  );

  return (
    <div className="platform-page">
      <PageHeader eyebrow={copy.eyebrow} title={copy.title} />
      <p className="mt-0 mb-4 text-sm text-muted">{copy.recordScope}</p>

      <VoicemailFilterBar filters={filters} onChange={applyFilters} />

      <div className="mt-4">
        {voicemailsQuery.isPending ? (
          <LoadingState label={copy.loading} />
        ) : voicemailsQuery.isError ? (
          <div className="grid gap-3">
            <ErrorState error={voicemailsQuery.error} fallback={copy.loadFailed} />
            <Button
              variant="secondary"
              size="sm"
              className="justify-self-start"
              onPress={() => void voicemailsQuery.refetch()}
            >
              {copy.retry}
            </Button>
          </div>
        ) : items.length === 0 && cursors.length > 1 ? (
          // A full page pointed here, so this is the list's end, not an empty
          // inbox: no "nothing yet" text, and Previous stays as the way back.
          <>
            <EmptyState label={copy.noMore} />
            {pagingNav}
          </>
        ) : items.length === 0 ? (
          <EmptyState label={hasVoicemailFilters(filters) ? copy.noMatch : copy.noneYet} />
        ) : (
          <>
            <Table aria-busy={voicemailsQuery.isFetching}>
              <Table.ScrollContainer>
                <Table.Content aria-label={copy.tableLabel}>
                  <Table.Header>
                    <Table.Column isRowHeader>{copy.columnReceived}</Table.Column>
                    <Table.Column>{copy.columnCaller}</Table.Column>
                    <Table.Column>{copy.columnCategory}</Table.Column>
                    <Table.Column>{copy.columnPriority}</Table.Column>
                    <Table.Column>{copy.columnStatus}</Table.Column>
                    <Table.Column>{copy.columnFollowUp}</Table.Column>
                    <Table.Column>{copy.columnDuration}</Table.Column>
                    {/* Holds each row's Details button; no heading of its own. */}
                    <Table.Column aria-label={copy.columnActions}>{""}</Table.Column>
                  </Table.Header>
                  <Table.Body>
                    {items.map((item) => (
                      <Table.Row key={item.id} id={item.id}>
                        <Table.Cell>
                          <time dateTime={item.received_at}>
                            {formatDate(item.received_at, lang)}
                          </time>
                        </Table.Cell>
                        <Table.Cell>
                          {/* User content: the isolate marks keep the number's
                              order inside a right-to-left row. */}
                          <bdi>{callerOf(item)}</bdi>
                        </Table.Cell>
                        <Table.Cell>
                          {item.category ? voicemailCategoryLabel(item.category, lang) : copy.notClassified}
                        </Table.Cell>
                        <Table.Cell>
                          {item.priority ? (
                            <PriorityChip priority={item.priority} lang={lang} />
                          ) : (
                            copy.notClassified
                          )}
                        </Table.Cell>
                        <Table.Cell>{voicemailStatusLabel(item.status, lang)}</Table.Cell>
                        <Table.Cell>{followUpStateLabel(item.follow_up_state, lang)}</Table.Cell>
                        <Table.Cell>{formatDuration(item.duration_seconds, lang)}</Table.Cell>
                        <Table.Cell>
                          <Button
                            variant="secondary"
                            size="sm"
                            onPress={() => openVoicemail(item.id)}
                          >
                            {copy.detailsFor(callerOf(item), formatDate(item.received_at, lang))}
                          </Button>
                        </Table.Cell>
                      </Table.Row>
                    ))}
                  </Table.Body>
                </Table.Content>
              </Table.ScrollContainer>
            </Table>
            {pagingNav}
          </>
        )}
      </div>

      <VoicemailDrawer voicemailId={openId} onClose={closeVoicemail} />
    </div>
  );
}
