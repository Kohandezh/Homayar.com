import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { Alert, Button } from "@heroui/react";

import { EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/platform/Ui";
import TrendCallsDrawer from "@/components/trends/TrendCallsDrawer";
import TrendCoverageBlock from "@/components/trends/TrendCoverageBlock";
import TrendFilterBar from "@/components/trends/TrendFilterBar";
import TrendTable, { type TrendDrillTarget } from "@/components/trends/TrendTable";
import { dataGapLabel, TRENDS_COPY } from "@/components/trends/trends-copy";
import { useI18n } from "@/lib/i18n-context";
import { useTeams } from "@/lib/platform-api";
import {
  readTrendFilters,
  trendQueryParams,
  writeTrendFilters,
  type TrendFilters,
} from "@/lib/trend-filters";
import { useIssueTrends } from "@/lib/trends-api";

/**
 * Issue trends (HOM-237): top call reasons, unresolved outcomes and
 * complaints over time. Every number comes from GET /trends/issues with its
 * denominator and coverage; the browser only turns a given ratio into a
 * percent. A computed cell opens the calls it was counted from.
 */
export default function TrendsPage() {
  const { lang } = useI18n();
  const copy = TRENDS_COPY[lang];
  const [searchParams, setSearchParams] = useSearchParams();
  // One moment for the whole visit, so the presets and the query key stay put.
  const [now] = useState(() => new Date());
  const filters = useMemo(() => readTrendFilters(searchParams, now), [searchParams, now]);
  const params = useMemo(() => trendQueryParams(filters), [filters]);
  const trendsQuery = useIssueTrends(params);
  const teamsQuery = useTeams();
  const [target, setTarget] = useState<TrendDrillTarget | null>(null);
  const data = trendsQuery.data;

  function applyFilters(next: TrendFilters) {
    setTarget(null);
    setSearchParams(writeTrendFilters(next, searchParams));
  }

  return (
    <div className="platform-page">
      <PageHeader eyebrow={copy.eyebrow} title={copy.title} />
      <p className="mt-0 mb-4 text-sm text-muted">{copy.intro}</p>

      <TrendFilterBar filters={filters} now={now} teams={teamsQuery.data?.items ?? []} onChange={applyFilters} />

      <div className="mt-4 grid gap-4">
        {trendsQuery.isPending ? (
          <LoadingState label={copy.loading} />
        ) : trendsQuery.isError ? (
          <div className="grid gap-3">
            <ErrorState error={trendsQuery.error} fallback={copy.loadFailed} />
            <Button
              variant="secondary"
              size="sm"
              className="justify-self-start"
              onPress={() => void trendsQuery.refetch()}
            >
              {copy.retry}
            </Button>
          </div>
        ) : data ? (
          <>
            <TrendCoverageBlock coverage={data.coverage} population={data.population} />

            {data.scope.restricted && (
              <Alert status="accent">
                <Alert.Indicator aria-hidden="true" />
                <Alert.Content>
                  <Alert.Description>{copy.restrictedScope}</Alert.Description>
                </Alert.Content>
              </Alert>
            )}
            {data.notices.includes("correlation_not_cause") && (
              <p className="m-0 text-sm text-muted">{copy.correlationNotCause}</p>
            )}

            {data.missing_data.indicators.length > 0 && (
              <section aria-labelledby="trend-data-gaps" className="grid gap-1">
                <h2 id="trend-data-gaps" className="m-0 text-sm font-semibold">
                  {copy.dataGaps}
                </h2>
                <ul className="m-0 grid gap-0.5 ps-5 text-sm">
                  {data.missing_data.indicators.map((code) => (
                    <li key={code}>
                      <bdi>{dataGapLabel(code, lang)}</bdi>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {data.rows.length === 0 ? (
              <EmptyState label={copy.empty} />
            ) : (
              <>
                {data.disclosure.masked.includes("window") && (
                  <p role="note" className="m-0 text-sm">
                    {copy.maskedWindow}
                  </p>
                )}
                {data.disclosure.masked.includes("points") && (
                  <p role="note" className="m-0 text-sm">
                    {copy.maskedPoints}
                  </p>
                )}
                <TrendTable data={data} onDrill={setTarget} />
              </>
            )}
          </>
        ) : null}
      </div>

      <TrendCallsDrawer target={target} trendParams={params} onClose={() => setTarget(null)} />
    </div>
  );
}
