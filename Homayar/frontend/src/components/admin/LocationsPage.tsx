import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Card, Chip, Table } from "@heroui/react";
import { Pencil, Plus, Trash2 } from "lucide-react";

import { EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/platform/Ui";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import { useLocations } from "@/lib/locations-api";
import { useCan } from "@/lib/use-can";
import type { Location } from "@/types/locations";
import LocationDeleteDialog from "@/components/admin/locations/LocationDeleteDialog";
import LocationDrawer from "@/components/admin/locations/LocationDrawer";
import LocationsFilterBar from "@/components/admin/locations/LocationsFilterBar";
import {
  NO_LOCATIONS_FILTERS,
  toLocationParams,
  type LocationsFilterState,
} from "@/components/admin/locations/location-model";
import { LOCATIONS_COPY, LOCATION_ERROR_CODES, locationKindLabel, locationWarningText } from "@/components/admin/locations/locations-copy";

/** The city filter commits 300 ms after the last keystroke. */
const CITY_DEBOUNCE_MS = 300;

/** What a finished save left behind: the name it saved under, and its warnings. */
interface SaveWarnings {
  name: string;
  warnings: string[];
}

interface EditingLocation {
  mode: "create" | "edit";
  location: Location | null;
}

/**
 * The service locations page (HOM-191): the workspace's branches, dealers and
 * service centers in one table, with filters and, for managers, a create and
 * edit drawer plus the delete confirmation.
 */
export default function LocationsPage() {
  const { workspace } = useAuth();
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const copy = LOCATIONS_COPY[lang];
  const can = useCan();
  const canManage = can.has("workspace:manage");

  const [filters, setFilters] = useState<LocationsFilterState>(NO_LOCATIONS_FILTERS);
  const [cityInput, setCityInput] = useState("");
  const [editing, setEditing] = useState<EditingLocation | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Location | null>(null);
  const [saveWarnings, setSaveWarnings] = useState<SaveWarnings | null>(null);
  // Escape closes the drawer, so the focus it took has to go back by hand.
  const openButtonRef = useRef<HTMLButtonElement | null>(null);

  // The trimmed text is what the request sends; the raw text is what the user
  // still sees while typing.
  useEffect(() => {
    const timer = setTimeout(() => {
      const committed = cityInput.trim();
      setFilters((previous) => (previous.city === committed ? previous : { ...previous, city: committed }));
    }, CITY_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [cityInput]);

  // Escape closes the drawer, so the focus it took has to go back by hand.
  // The drawer's own focus scope restores focus while it unmounts, so the
  // opener's focus is returned one tick later, after the drawer is really
  // gone; calling focus() in the close handler would be undone by the scope.
  const drawerWasOpen = useRef(false);
  useEffect(() => {
    if (editing !== null) {
      drawerWasOpen.current = true;
      return;
    }
    if (!drawerWasOpen.current) return;
    drawerWasOpen.current = false;
    const timer = window.setTimeout(() => openButtonRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [editing]);

  const params = useMemo(() => toLocationParams(filters), [filters]);
  const locationsQuery = useLocations(params);
  const locations = useMemo(() => locationsQuery.data?.items ?? [], [locationsQuery.data]);
  const hasFilters = Object.keys(params).length > 0;

  function closeDrawer() {
    setEditing(null);
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
            <Button
              size="sm"
              variant="primary"
              onPress={(event) => {
                // Escape closes the drawer, so remember where the focus
                // should go back to.
                openButtonRef.current = event.target as HTMLButtonElement;
                setEditing({ mode: "create", location: null });
              }}
            >
              <Plus aria-hidden="true" size={15} />
              {copy.addLocation}
            </Button>
          ) : undefined
        }
      />
      <p className="m-0 text-sm text-muted">{copy.intro}</p>

      {saveWarnings && saveWarnings.warnings.length > 0 && (
        <Alert status="warning">
          <Alert.Indicator aria-hidden="true" />
          <Alert.Content>
            <Alert.Title>{copy.savedWithWarnings(saveWarnings.name)}</Alert.Title>
            <ul className="m-0 mt-1 list-disc space-y-0.5 ps-5 text-sm">
              {saveWarnings.warnings.map((code, index) => (
                <li key={`${index}-${code}`}>{locationWarningText(code, lang)}</li>
              ))}
            </ul>
            <Button size="sm" variant="ghost" className="mt-2" onPress={() => setSaveWarnings(null)}>
              {copy.dismiss}
            </Button>
          </Alert.Content>
        </Alert>
      )}

      <Card<"section"> render={(props) => <section {...props} />} className="min-w-0">
        <Card.Content className="min-w-0 space-y-4" aria-busy={locationsQuery.isPending || undefined}>
          <LocationsFilterBar
            filters={filters}
            cityInput={cityInput}
            onFiltersChange={setFilters}
            onCityInputChange={setCityInput}
          />

          {locationsQuery.isPending ? (
            <LoadingState label={copy.loading} />
          ) : locationsQuery.isError ? (
            <ErrorState error={locationsQuery.error} codes={LOCATION_ERROR_CODES} fallback={copy.loadFailed} />
          ) : locations.length === 0 ? (
            <div className="space-y-2">
              <EmptyState label={hasFilters ? copy.noMatch : copy.noneYet} />
              {!hasFilters && canManage && <p className="m-0 text-center text-sm text-muted">{copy.addFirst}</p>}
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
                      {copy.columnName}
                    </Table.Column>
                    <Table.Column id="kind">{copy.columnKind}</Table.Column>
                    <Table.Column id="city">{copy.columnCity}</Table.Column>
                    <Table.Column id="phone">{copy.columnPhone}</Table.Column>
                    <Table.Column id="status">{copy.columnStatus}</Table.Column>
                    <Table.Column id="actions">{copy.columnActions}</Table.Column>
                  </Table.Header>
                  <Table.Body items={locations} dependencies={[lang, canManage]}>
                    {(item: Location) => (
                      <Table.Row id={item.id}>
                        <Table.Cell>
                          <span className="block font-semibold text-foreground" dir="auto">
                            {item.name}
                          </span>
                          <span className="mt-0.5 block truncate text-xs text-muted" dir="ltr">
                            {item.code}
                          </span>
                        </Table.Cell>
                        <Table.Cell className="whitespace-nowrap text-xs text-muted">
                          {locationKindLabel(item.kind, lang)}
                        </Table.Cell>
                        <Table.Cell>
                          <span className="text-sm" dir="auto">
                            {item.city ?? "—"}
                          </span>
                        </Table.Cell>
                        <Table.Cell>
                          <span className="text-sm" dir="ltr">
                            {item.phone ?? "—"}
                          </span>
                        </Table.Cell>
                        <Table.Cell>
                          <Chip color={item.is_active ? "success" : "default"} variant="soft" size="sm">
                            <Chip.Label>{item.is_active ? copy.active : copy.inactive}</Chip.Label>
                          </Chip>
                        </Table.Cell>
                        <Table.Cell>
                          {canManage && (
                            <div className="flex flex-wrap items-center gap-2">
                              <Button
                                size="sm"
                                variant="ghost"
                                aria-label={copy.editLocation(item.name)}
                                onPress={(event) => {
                                  openButtonRef.current = event.target as HTMLButtonElement;
                                  setEditing({ mode: "edit", location: item });
                                }}
                              >
                                <Pencil aria-hidden="true" size={14} />
                                {copy.edit}
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                className="text-danger"
                                aria-label={copy.removeLocation(item.name)}
                                onPress={() => setDeleteTarget(item)}
                              >
                                <Trash2 aria-hidden="true" size={14} />
                                {copy.remove}
                              </Button>
                            </div>
                          )}
                        </Table.Cell>
                      </Table.Row>
                    )}
                  </Table.Body>
                </Table.Content>
              </Table.ScrollContainer>
            </Table>
          )}
        </Card.Content>
      </Card>

      {canManage && editing && (
        <LocationDrawer
          mode={editing.mode}
          location={editing.location}
          onClose={closeDrawer}
          onSaved={(response) => setSaveWarnings({ name: response.name, warnings: response.warnings ?? [] })}
        />
      )}
      {canManage && deleteTarget && (
        <LocationDeleteDialog location={deleteTarget} onClose={() => setDeleteTarget(null)} />
      )}
    </div>
  );
}
