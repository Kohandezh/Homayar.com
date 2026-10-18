import { useMemo, useRef, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { Alert, Button, Card, Chip, Skeleton, Table, linkVariants } from "@heroui/react";
import { Link as RouterLink } from "react-router";
import { Pencil, Plus, PowerOff, RotateCcw } from "lucide-react";

import { EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/platform/Ui";
import { formatNumber } from "@/lib/format";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import { getTeam, platformQueryKeys, useTeams, useWorkspaceSettings } from "@/lib/platform-api";
import { useCan } from "@/lib/use-can";
import type { Team } from "@/types/platform";
import TeamDrawer from "@/components/admin/teams/TeamDrawer";
import TeamNameDialog from "@/components/admin/teams/TeamNameDialog";
import TeamStatusDialog from "@/components/admin/teams/TeamStatusDialog";
import { effectiveMemberCount } from "@/components/admin/teams/team-model";
import { TEAM_ERROR_CODES, TEAMS_COPY } from "@/components/admin/teams/teams-copy";

/** HeroUI link styles for the react-router link (keeps the /app/ basename). */
const scopeLinkClass = linkVariants().base();

/**
 * The record-scope explainer. Only owners and administrators ask for it:
 * the component itself (and its request) exists just for them, so a member
 * never triggers `GET /admin/workspace-settings`.
 */
function RecordScopeBanner() {
  const { lang } = useI18n();
  const copy = TEAMS_COPY[lang];
  const settingsQuery = useWorkspaceSettings();
  // While loading: neither the text nor the failure exists yet.
  if (settingsQuery.isPending) return null;
  if (settingsQuery.isError) {
    return (
      <Alert status="danger" role="alert">
        <Alert.Indicator aria-hidden="true" />
        <Alert.Content>
          <Alert.Description>{copy.scopeLoadFailed}</Alert.Description>
        </Alert.Content>
      </Alert>
    );
  }
  const teamScoped = settingsQuery.data.record_scope === "team";
  return (
    <Alert status={teamScoped ? "warning" : "accent"}>
      <Alert.Indicator aria-hidden="true" />
      <Alert.Content>
        <Alert.Description>
          {teamScoped ? copy.scopeTeam : copy.scopeWorkspace}{" "}
          <RouterLink to="/admin/settings" className={scopeLinkClass}>
            {copy.scopeLink}
          </RouterLink>
        </Alert.Description>
      </Alert.Content>
    </Alert>
  );
}

/** The member count of one team, or nothing yet, or a dash when it failed. */
function MemberCountCell({ state, lang }: { state: "loading" | "error" | number; lang: "fa" | "en" }) {
  if (state === "loading") return <Skeleton className="h-4 w-6" aria-hidden="true" />;
  if (state === "error") return <span>{TEAMS_COPY[lang].membersLoadFailed}</span>;
  return <span>{formatNumber(state, lang)}</span>;
}

/**
 * The teams page (HOM-182). The list comes from `GET /teams`; the member
 * counts each read that team's detail once, through the same cache entry the
 * drawer reuses when a row is opened.
 */
export default function TeamsPage() {
  const { workspace } = useAuth();
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const copy = TEAMS_COPY[lang];
  const can = useCan();
  const canManage = can.has("team:manage");
  const canSeeScope = can.has("workspace:manage");

  const teamsQuery = useTeams();
  const teams = useMemo(() => teamsQuery.data?.items ?? [], [teamsQuery.data]);

  // One detail read per team, under the same key the drawer uses.
  const detailQueries = useQueries({
    queries: teams.map((team) => ({
      queryKey: platformQueryKeys.team(team.id),
      queryFn: ({ signal }: { signal: AbortSignal }) => getTeam(team.id, signal),
    })),
  });

  const [openTeamId, setOpenTeamId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState<Team | null>(null);
  const [statusTarget, setStatusTarget] = useState<Team | null>(null);
  // Escape closes the drawer, so the focus it took has to go back by hand.
  const openButtonRef = useRef<HTMLButtonElement | null>(null);

  const openTeam = teams.find((team) => team.id === openTeamId) ?? null;

  // A primitive signature per render, so React Aria's cached row cells see
  // count changes without comparing query objects.
  const countStates = detailQueries.map((query) =>
    query.isPending ? "…" : query.isError ? "!" : String(effectiveMemberCount(query.data.assignments)),
  );

  function closeDrawer() {
    setOpenTeamId(null);
    openButtonRef.current?.focus();
  }

  const tableLabel = copy.tableLabel;

  return (
    // A div, not a main: the admin layout already provides the landmark.
    <div className="platform-page space-y-5">
      <PageHeader
        eyebrow={workspace?.name ?? (isFa ? "فضای کاری" : "Workspace")}
        title={copy.title}
        actions={
          canManage ? (
            <Button size="sm" variant="primary" onPress={() => setCreating(true)}>
              <Plus aria-hidden="true" size={15} />
              {copy.createTeam}
            </Button>
          ) : undefined
        }
      />
      <p className="m-0 text-sm text-muted">{copy.intro}</p>

      {canSeeScope && <RecordScopeBanner />}

      <Card<"section"> render={(props) => <section {...props} />} className="min-w-0">
        <Card.Content className="min-w-0" aria-busy={teamsQuery.isPending || undefined}>
          {teamsQuery.isPending ? (
            <LoadingState label={copy.loading} />
          ) : teamsQuery.isError ? (
            <ErrorState
              error={teamsQuery.error}
              codes={TEAM_ERROR_CODES}
              fallback={copy.loadFailed}
            />
          ) : teams.length === 0 ? (
            <div className="space-y-2">
              <EmptyState label={copy.noTeams} />
              {canManage && <p className="m-0 text-center text-sm text-muted">{copy.createFirst}</p>}
            </div>
          ) : (
            <Table className="min-w-0">
              {/* The table scrolls sideways on a narrow screen, so the region
                  takes focus for a keyboard user (as the members table does). */}
              <Table.ScrollContainer
                role="region"
                tabIndex={0}
                aria-label={copy.tableScrollLabel}
                className="platform-members-table-scroll"
              >
                <Table.Content aria-label={tableLabel}>
                  <Table.Header>
                    <Table.Column id="name" isRowHeader>
                      {copy.columnTeam}
                    </Table.Column>
                    <Table.Column id="status">{copy.columnStatus}</Table.Column>
                    <Table.Column id="members">{copy.columnMembers}</Table.Column>
                    <Table.Column id="actions">{copy.columnActions}</Table.Column>
                  </Table.Header>
                  <Table.Body items={teams} dependencies={[lang, canManage, countStates.join(",")]}>
                    {(team: Team) => {
                      const index = teams.findIndex((candidate) => candidate.id === team.id);
                      const detail = detailQueries[index];
                      const countState: "loading" | "error" | number = detail
                        ? detail.isPending
                          ? "loading"
                          : detail.isError
                            ? "error"
                            : effectiveMemberCount(detail.data.assignments)
                        : "loading";
                      return (
                        <Table.Row id={team.id}>
                          <Table.Cell>
                            <span className="font-semibold text-foreground" dir="auto">
                              {team.name}
                            </span>
                          </Table.Cell>
                          <Table.Cell>
                            <Chip color={team.is_active ? "success" : "default"} variant="soft" size="sm">
                              <Chip.Label>{team.is_active ? copy.active : copy.inactive}</Chip.Label>
                            </Chip>
                          </Table.Cell>
                          <Table.Cell>
                            <MemberCountCell state={countState} lang={lang} />
                          </Table.Cell>
                          <Table.Cell>
                            <div className="flex flex-wrap items-center gap-2">
                              <Button
                                size="sm"
                                variant="ghost"
                                aria-label={copy.openTeam(team.name)}
                                onPress={(event) => {
                                  // Escape closes the drawer, so remember where
                                  // the focus should go back to.
                                  openButtonRef.current = event.target as HTMLButtonElement;
                                  setOpenTeamId(team.id);
                                }}
                              >
                                {copy.open}
                              </Button>
                              {canManage && (
                                <>
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    aria-label={copy.renameTeam(team.name)}
                                    onPress={() => setRenaming(team)}
                                  >
                                    <Pencil aria-hidden="true" size={14} />
                                    {copy.rename}
                                  </Button>
                                  {team.is_active ? (
                                    <Button
                                      size="sm"
                                      variant="ghost"
                                      className="text-danger"
                                      aria-label={copy.deactivateTeam(team.name)}
                                      onPress={() => setStatusTarget(team)}
                                    >
                                      <PowerOff aria-hidden="true" size={14} />
                                      {copy.deactivate}
                                    </Button>
                                  ) : (
                                    <Button
                                      size="sm"
                                      variant="ghost"
                                      aria-label={copy.reactivateTeam(team.name)}
                                      onPress={() => setStatusTarget(team)}
                                    >
                                      <RotateCcw aria-hidden="true" size={14} />
                                      {copy.reactivate}
                                    </Button>
                                  )}
                                </>
                              )}
                            </div>
                          </Table.Cell>
                        </Table.Row>
                      );
                    }}
                  </Table.Body>
                </Table.Content>
              </Table.ScrollContainer>
            </Table>
          )}
        </Card.Content>
      </Card>

      <TeamDrawer team={openTeam} canManage={canManage} onClose={closeDrawer} />
      {canManage && creating && <TeamNameDialog mode="create" team={null} onClose={() => setCreating(false)} />}
      {canManage && renaming && <TeamNameDialog mode="rename" team={renaming} onClose={() => setRenaming(null)} />}
      {canManage && statusTarget && <TeamStatusDialog team={statusTarget} onClose={() => setStatusTarget(null)} />}
    </div>
  );
}
