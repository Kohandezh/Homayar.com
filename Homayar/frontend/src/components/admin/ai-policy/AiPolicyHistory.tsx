import { useId, useState } from "react";
import { Button, Table } from "@heroui/react";

import { AI_POLICY_COPY, kindListText } from "@/components/admin/ai-policy/ai-policy-copy";
import Ids from "@/components/admin/ai-policy/Ids";
import { diffPolicies, type PolicyDiffEntry, type PolicyDiffField } from "@/components/admin/ai-policy/ai-policy-model";
import { EmptyState, ErrorState, LoadingState } from "@/components/platform/Ui";
import { formatDate, formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import type { AiEndpointKind, AiPolicy, AiPolicyResponse } from "@/types/ai-policy";

/** One value of the comparison, in the plain words of the read view. */
function DiffValue({
  field,
  value,
}: {
  field: PolicyDiffField;
  value: PolicyDiffEntry["selected"];
}) {
  const { lang } = useI18n();
  const copy = AI_POLICY_COPY[lang];
  if (field === "endpoint_kinds") {
    return <span>{kindListText(copy, value as readonly AiEndpointKind[])}</span>;
  }
  if (field === "regions") {
    const ids = value as readonly string[];
    return ids.length > 0 ? <Ids ids={ids} /> : <span>—</span>;
  }
  if (field === "manifests") {
    // An empty model list reads like the read view (B3), not as a dash.
    const ids = value as readonly string[];
    return ids.length > 0 ? <Ids ids={ids} /> : <span>{copy.anyManifest}</span>;
  }
  if (field === "fallback") {
    return <span>{value ? copy.allowed : copy.notAllowed}</span>;
  }
  return <span>{value === "redacted" ? copy.redacted : copy.original}</span>;
}

/** The stored versions, newest first, with a diff against the current policy. */
export default function AiPolicyHistory({
  current,
  items,
  memberNames,
  isLoading,
  error,
  onRetry,
}: {
  current: AiPolicy;
  items: AiPolicyResponse[];
  memberNames: Map<string, string>;
  isLoading: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  const { lang } = useI18n();
  const copy = AI_POLICY_COPY[lang];
  const headingId = useId();
  const [compareVersion, setCompareVersion] = useState<number | null>(null);
  const compared = items.find((item) => item.version === compareVersion) ?? null;
  const diff = compared ? diffPolicies(compared.policy, current) : [];

  return (
    <section className="mt-6" aria-labelledby={headingId}>
      <h2 id={headingId} className="m-0 text-sm font-semibold text-text-primary">
        {copy.history}
      </h2>
      <p className="mt-1 mb-4 text-sm text-muted">{copy.historyIntro}</p>

      {error !== null ? (
        <div className="grid gap-3">
          <ErrorState error={error} fallback={copy.historyError} />
          <Button variant="secondary" size="sm" className="justify-self-start" onPress={onRetry}>
            {copy.tryAgain}
          </Button>
        </div>
      ) : isLoading ? (
        <LoadingState label={copy.loading} />
      ) : items.length === 0 ? (
        <EmptyState label={copy.noVersions} />
      ) : (
        <>
          <Table>
            <Table.ScrollContainer>
              <Table.Content aria-label={copy.history}>
                <Table.Header>
                  <Table.Column isRowHeader>{copy.columns.version}</Table.Column>
                  <Table.Column>{copy.columns.reason}</Table.Column>
                  <Table.Column>{copy.columns.changedBy}</Table.Column>
                  <Table.Column>{copy.columns.date}</Table.Column>
                  {/* Holds each row's compare button; no heading of its own. */}
                  <Table.Column aria-label={copy.actions}>{""}</Table.Column>
                </Table.Header>
                <Table.Body>
                  {items.map((item) => {
                    const version = formatNumber(item.version, lang);
                    return (
                      <Table.Row key={item.version} id={String(item.version)}>
                        <Table.Cell>{version}</Table.Cell>
                        <Table.Cell>
                          {/* Free text and names: a bare bdi lets the browser
                              pick the direction from the content. */}
                          {item.reason ? <bdi>{item.reason}</bdi> : "—"}
                        </Table.Cell>
                        <Table.Cell>
                          {item.created_by_user_id ? (
                            <bdi>{memberNames.get(item.created_by_user_id) ?? "—"}</bdi>
                          ) : (
                            "—"
                          )}
                        </Table.Cell>
                        <Table.Cell>
                          {item.created_at && (
                            <time dateTime={item.created_at}>{formatDate(item.created_at, lang)}</time>
                          )}
                        </Table.Cell>
                        <Table.Cell>
                          <Button
                            variant="secondary"
                            size="sm"
                            onPress={() =>
                              setCompareVersion(item.version === compareVersion ? null : item.version)
                            }
                          >
                            {copy.compare(version)}
                          </Button>
                        </Table.Cell>
                      </Table.Row>
                    );
                  })}
                </Table.Body>
              </Table.Content>
            </Table.ScrollContainer>
          </Table>

          {compared && (
            <div className="platform-surface mt-4 p-4">
              <h3 className="m-0 text-sm font-semibold text-text-primary">
                {copy.compareHeading(formatNumber(compared.version, lang))}
              </h3>
              {diff.length === 0 ? (
                <p className="mt-2 mb-0 text-sm">
                  {copy.compareSame(formatNumber(compared.version, lang))}
                </p>
              ) : (
                <ul className="mt-2 mb-0 grid gap-2 ps-5 text-sm">
                  {diff.map((entry) => (
                    <li key={`${entry.stage}:${entry.field}`} className="grid gap-0.5">
                      <span className="font-medium">
                        <span>{copy.stages[entry.stage]}</span>
                        <span aria-hidden="true"> · </span>
                        <span>{copy.fields[entry.field]}</span>
                      </span>
                      <span>
                        {copy.compareSelected}: <DiffValue field={entry.field} value={entry.selected} />
                      </span>
                      <span>
                        {copy.compareCurrent}: <DiffValue field={entry.field} value={entry.current} />
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
