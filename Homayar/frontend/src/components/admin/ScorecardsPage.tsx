import { useMemo, useState } from "react";
import { Button } from "@heroui/react";
import { PencilLine, Plus } from "lucide-react";
import { useSearchParams } from "react-router";

import ScorecardDefinitionView from "@/components/admin/scorecards/ScorecardDefinitionView";
import ScorecardEditForm from "@/components/admin/scorecards/ScorecardEditForm";
import ScorecardHistory from "@/components/admin/scorecards/ScorecardHistory";
import ScorecardList from "@/components/admin/scorecards/ScorecardList";
import { KEY_PATTERN } from "@/components/admin/scorecards/scorecard-model";
import { SCORECARDS_COPY } from "@/components/admin/scorecards/scorecards-copy";
import { ErrorState, LoadingState, PageHeader } from "@/components/platform/Ui";
import { apiErrorInfo } from "@/lib/api-errors";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useMembers } from "@/lib/platform-api";
import { useScorecardVersion, useScorecardVersions, useScorecards } from "@/lib/scorecards-api";
import { useCan } from "@/lib/use-can";
import type { ScorecardVersionResponse } from "@/types/scorecards";

/** `?version=`: a stored version number, absent (the latest), or a value no version can have. */
function versionFromParam(value: string | null): number | "invalid" | null {
  if (value === null) return null;
  return /^[1-9]\d{0,8}$/.test(value) ? Number(value) : "invalid";
}

function isNotFound(error: unknown) {
  return apiErrorInfo(error).status === 404;
}

/**
 * The QA scorecards (HOM-248). Every role reads the list, a scorecard's
 * versions and its preview; owners and admins (`scorecard:manage`) also
 * create scorecards and publish new versions. A published version is never
 * edited: the editor always appends a new version.
 */
export default function ScorecardsPage() {
  const { lang } = useI18n();
  const copy = SCORECARDS_COPY[lang];
  const canManage = useCan().has("scorecard:manage");
  const [params, setParams] = useSearchParams();
  const keyParam = params.get("key") || null;
  // A key no scorecard can have is "not found" and never reaches an API path.
  const keyInvalid = keyParam !== null && !KEY_PATTERN.test(keyParam);
  const key = keyInvalid ? null : keyParam;
  const versionParam = versionFromParam(params.get("version"));
  const listQuery = useScorecards();
  const versionsQuery = useScorecardVersions(key);
  const membersQuery = useMembers();
  // The editor belongs to the scorecard it was opened on (null: a new one).
  const [editor, setEditor] = useState<{ base: ScorecardVersionResponse | null } | null>(null);
  const [publishedVersion, setPublishedVersion] = useState<number | null>(null);

  const listItem = listQuery.data?.items.find((item) => item.scorecard_key === key) ?? null;
  const latestVersion = versionsQuery.data?.items[0]?.version ?? listItem?.version ?? null;
  const shownVersion = versionParam === "invalid" ? null : (versionParam ?? latestVersion);
  const detailQuery = useScorecardVersion(key, shownVersion);
  const detail = detailQuery.data ?? null;
  const isCurrent = shownVersion !== null && shownVersion === latestVersion;

  // A failed members read leaves the names unknown; the history shows the dash.
  const memberNames = useMemo(
    () =>
      new Map(
        (membersQuery.data?.items ?? [])
          .filter((member) => member.display_name)
          .map((member) => [member.user_id, member.display_name]),
      ),
    [membersQuery.data],
  );

  const editorOpen =
    canManage && editor !== null && (editor.base === null || editor.base.scorecard_key === key);

  function viewVersion(version: number | null) {
    setEditor(null);
    setPublishedVersion(null);
    if (!key) return;
    setParams(version === null ? { key } : { key, version: String(version) });
  }

  const scorecardMissing = key !== null && versionsQuery.isError && isNotFound(versionsQuery.error);

  const history = key !== null && !scorecardMissing && (
    <ScorecardHistory
      items={versionsQuery.data?.items ?? []}
      memberNames={memberNames}
      isLoading={versionsQuery.isPending}
      // A failed refetch with cached data keeps the table.
      error={versionsQuery.isError && !versionsQuery.data ? versionsQuery.error : null}
      onRetry={() => void versionsQuery.refetch()}
      onView={(version) => viewVersion(version)}
    />
  );

  function renderDetail() {
    if (keyInvalid) return <p className="m-0 text-sm">{copy.notFound}</p>;
    if (key === null) return <p className="m-0 text-sm text-muted">{copy.pickHint}</p>;
    if (scorecardMissing) return <p className="m-0 text-sm">{copy.notFound}</p>;

    let body;
    if (versionParam === "invalid" || (detailQuery.isError && isNotFound(detailQuery.error))) {
      body = <p className="m-0 text-sm">{copy.versionNotFound}</p>;
    } else if (detailQuery.isError && !detail) {
      body = (
        <div className="grid gap-3">
          <ErrorState error={detailQuery.error} fallback={copy.detailError} />
          <Button
            variant="secondary"
            size="sm"
            className="justify-self-start"
            onPress={() => void detailQuery.refetch()}
          >
            {copy.tryAgain}
          </Button>
        </div>
      );
    } else if (!detail) {
      body = <LoadingState label={copy.loading} />;
    } else {
      body = (
        <div className="grid gap-4">
          {(!isCurrent || canManage) && (
            <div className="flex flex-wrap items-center gap-3">
              {!isCurrent && latestVersion !== null && (
                <>
                  <p className="m-0 text-sm">
                    {copy.viewingOld(formatNumber(detail.version, lang), formatNumber(latestVersion, lang))}
                  </p>
                  <Button variant="secondary" size="sm" onPress={() => viewVersion(null)}>
                    {copy.viewCurrent}
                  </Button>
                </>
              )}
              {isCurrent && canManage && (
                <Button
                  variant="secondary"
                  size="sm"
                  onPress={() => {
                    setPublishedVersion(null);
                    setEditor({ base: detail });
                  }}
                >
                  <PencilLine aria-hidden="true" size={15} />
                  {copy.edit}
                </Button>
              )}
            </div>
          )}
          <ScorecardDefinitionView definition={detail.definition} />
        </div>
      );
    }

    return (
      <>
        {body}
        {history}
      </>
    );
  }

  return (
    <div className="platform-page">
      <PageHeader
        eyebrow={copy.eyebrow}
        title={copy.heading}
        actions={
          canManage ? (
            <Button
              variant="secondary"
              size="sm"
              onPress={() => {
                setPublishedVersion(null);
                setEditor({ base: null });
              }}
            >
              <Plus aria-hidden="true" size={15} />
              {copy.newScorecard}
            </Button>
          ) : (
            <span className="text-xs text-muted">{copy.readOnlyHint}</span>
          )
        }
      />
      <p className="mt-0 mb-4 text-sm text-muted">{copy.intro}</p>

      {publishedVersion !== null && (
        <p
          role="status"
          className="mb-4 rounded-md border border-success/30 bg-success/5 px-3 py-2 text-xs text-success"
        >
          {copy.published(formatNumber(publishedVersion, lang))}
        </p>
      )}

      {listQuery.isPending ? (
        <LoadingState label={copy.loading} />
      ) : listQuery.isError && !listQuery.data ? (
        // Only a read with no data at all swaps the page for the error state.
        <div className="grid gap-3">
          <ErrorState error={listQuery.error} fallback={copy.loadError} />
          <Button
            variant="secondary"
            size="sm"
            className="justify-self-start"
            onPress={() => void listQuery.refetch()}
          >
            {copy.tryAgain}
          </Button>
        </div>
      ) : (
        <div className="grid items-start gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
          <ScorecardList
            items={listQuery.data?.items ?? []}
            selectedKey={key}
            onSelect={() => {
              setEditor(null);
              setPublishedVersion(null);
            }}
          />
          <div className="min-w-0">
            {editorOpen ? (
              <>
                <ScorecardEditForm
                  base={editor.base}
                  onPublished={(response) => {
                    setEditor(null);
                    setPublishedVersion(response.version);
                    setParams({ key: response.scorecard_key });
                  }}
                  onCancel={() => setEditor(null)}
                />
                {editor.base !== null && history}
              </>
            ) : (
              renderDetail()
            )}
          </div>
        </div>
      )}
    </div>
  );
}
