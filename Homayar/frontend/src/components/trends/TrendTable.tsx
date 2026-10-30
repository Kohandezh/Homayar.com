import { Chip, Table } from "@heroui/react";

import TrendCellView from "@/components/trends/TrendCellView";
import TrendSparkline from "@/components/trends/TrendSparkline";
import { periodLabel, trendRowKey, trendRowName } from "@/components/trends/trend-format";
import { TRENDS_COPY } from "@/components/trends/trends-copy";
import { useI18n } from "@/lib/i18n-context";
import type { IssueTrendResponse, TrendCell, TrendRow } from "@/types/trends";

/** The cell a drill-down opens; `point` is absent for the whole-range cell. */
export interface TrendDrillTarget {
  row: string;
  point?: number;
  rowName: string;
  period: string;
  cell: TrendCell;
}

interface TableRow {
  key: string;
  name: string;
  isFamily: boolean;
  window: TrendRow;
  /** One entry per point; undefined where the point has no such row. */
  periods: Array<TrendRow | undefined>;
}

/** The window rows grouped by family in the API's order: each family row, then its issues. */
function tableRows(data: IssueTrendResponse, lang: "fa" | "en"): TableRow[] {
  const families: string[] = [];
  for (const row of data.rows) if (!families.includes(row.family)) families.push(row.family);
  const byPoint = data.points.map((point) => new Map(point.rows.map((row) => [trendRowKey(row), row])));
  const ordered = families.flatMap((family) => [
    ...data.rows.filter((row) => row.family === family && row.level === "family"),
    ...data.rows.filter((row) => row.family === family && row.level === "issue"),
  ]);
  return ordered.map((row) => {
    const key = trendRowKey(row);
    return {
      key,
      name: trendRowName(row, lang),
      isFamily: row.level === "family",
      window: row,
      periods: byPoint.map((rows) => rows.get(key)),
    };
  });
}

/**
 * The trend table: the row name, the whole-range cell, one cell per period
 * and the trend line. A layer the API masked (HOM-82) has no column here;
 * the page says why above the table.
 */
export default function TrendTable({
  data,
  onDrill,
}: {
  data: IssueTrendResponse;
  onDrill: (target: TrendDrillTarget) => void;
}) {
  const { lang } = useI18n();
  const copy = TRENDS_COPY[lang];
  const showWindow = !data.disclosure.masked.includes("window");
  const showPoints = !data.disclosure.masked.includes("points");
  const periods = data.points.map((point) => periodLabel(point, data.series.timezone, lang));
  const rows = tableRows(data, lang);

  return (
    <Table>
      <Table.ScrollContainer>
        <Table.Content aria-label={copy.tableLabel}>
          <Table.Header>
            <Table.Column isRowHeader>{copy.columnName}</Table.Column>
            {showWindow ? <Table.Column id="window">{copy.columnWindow}</Table.Column> : null}
            {showPoints
              ? data.points.map((point, index) => (
                  <Table.Column key={point.from} id={`point-${index}`}>
                    <span className="grid gap-0.5">
                      <span>{periods[index]}</span>
                      {point.partial && (
                        <Chip size="sm" variant="soft" color="warning" className="justify-self-start">
                          <Chip.Label>{copy.partial}</Chip.Label>
                        </Chip>
                      )}
                    </span>
                  </Table.Column>
                ))
              : null}
            <Table.Column id="trend">{copy.columnTrend}</Table.Column>
          </Table.Header>
          <Table.Body>
            {rows.map((row) => (
              <Table.Row key={row.key} id={row.key}>
                <Table.Cell>
                  <span className={row.isFamily ? "font-semibold" : "ps-4"}>
                    <bdi>{row.name}</bdi>
                  </span>
                </Table.Cell>
                {showWindow ? (
                  <Table.Cell>
                    <TrendCellView
                      cell={row.window.cell}
                      pendingReview={row.window.pending_review}
                      showCallsLabel={copy.showCalls(row.name, copy.wholeRange)}
                      onShowCalls={() =>
                        onDrill({ row: row.key, rowName: row.name, period: copy.wholeRange, cell: row.window.cell })
                      }
                    />
                  </Table.Cell>
                ) : null}
                {showPoints
                  ? row.periods.map((period, index) => (
                      <Table.Cell key={data.points[index].from}>
                        {period ? (
                          <TrendCellView
                            cell={period.cell}
                            pendingReview={period.pending_review}
                            showCallsLabel={copy.showCalls(row.name, periods[index])}
                            onShowCalls={() =>
                              onDrill({
                                row: row.key,
                                point: index,
                                rowName: row.name,
                                period: periods[index],
                                cell: period.cell,
                              })
                            }
                          />
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </Table.Cell>
                    ))
                  : null}
                <Table.Cell>
                  <TrendSparkline
                    cells={showPoints ? row.periods.map((period) => period?.cell) : []}
                    label={copy.trendOf(row.name)}
                  />
                </Table.Cell>
              </Table.Row>
            ))}
          </Table.Body>
        </Table.Content>
      </Table.ScrollContainer>
    </Table>
  );
}
