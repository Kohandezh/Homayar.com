import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import LocationsPage from "@/components/admin/LocationsPage";
import api from "@/lib/api";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { AuthWorkspace, WorkspaceRole } from "@/types/auth";
import type { Location } from "@/types/locations";

const T = "2026-10-18T12:00:00Z";
const LOAD = { timeout: 5000 };

// The drawer walks many fields through real key events, and the full suite
// shares the machine with parallel tickets: give the flows room.
vi.setConfig({ testTimeout: 20000 });

function location(overrides: Partial<Location> = {}): Location {
  return {
    id: "loc-1",
    workspace_id: "ws-1",
    kind: "branch",
    name: "Vanak branch",
    code: "vanak",
    province: "Tehran",
    city: "Tehran",
    district: "Vanak",
    street: "Valiasr",
    postal_code: "1234567890",
    address_text: "No 1, Valiasr St",
    latitude: 35.75,
    longitude: 51.41,
    phone: "+982112345678",
    is_active: true,
    service_tags: ["oil", "tires"],
    opening_hours: null,
    created_at: T,
    updated_at: T,
    ...overrides,
  };
}

const SPARSE: Location = location({
  id: "loc-2",
  kind: "dealer",
  name: "Saadi dealer",
  code: "saadi",
  province: null,
  city: null,
  district: null,
  street: null,
  postal_code: null,
  address_text: null,
  latitude: null,
  longitude: null,
  phone: null,
  is_active: false,
  service_tags: [],
});

/** Serves the list read; anything else is a 404. */
function mockReads(items: Location[] = [location()]) {
  return vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url === "/locations") return okResponse({ items, total: items.length });
    throw apiError(404, "not_found");
  });
}

/** The params of the latest GET /locations, so filter steps can be asserted. */
function lastParams(spy: ReturnType<typeof mockReads>) {
  const calls = spy.mock.calls.filter(([url]) => url === "/locations");
  expect(calls.length).toBeGreaterThan(0);
  return calls[calls.length - 1]?.[1]?.params;
}

function workspace(role: WorkspaceRole, permissions?: string[]): AuthWorkspace {
  return { ...FIXTURE_WORKSPACE, role, ...(permissions ? { permissions } : {}) };
}

function renderPage(selected: AuthWorkspace = workspace("owner"), lang: "fa" | "en" = "en") {
  window.localStorage.removeItem("homayar_lang");
  return renderConsole(<LocationsPage />, {
    route: "/admin/locations",
    lang,
    auth: { status: "authenticated", user: FIXTURE_USER, workspace: selected, workspaces: [selected] },
  });
}

async function findRow(name: RegExp | string) {
  return screen.findByRole("rowheader", { name }, LOAD);
}

describe("LocationsPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("renders the list with code, kind, city, phone and status", async () => {
    mockReads([location(), SPARSE]);
    renderPage();

    const dense = (await findRow(/Vanak branch/)).closest("tr");
    expect(dense).not.toBeNull();
    expect(within(dense!).getByText("vanak")).toBeInTheDocument();
    expect(within(dense!).getByText("Branch")).toBeInTheDocument();
    expect(within(dense!).getByText("Tehran")).toBeInTheDocument();
    expect(within(dense!).getByText("+982112345678")).toBeInTheDocument();
    expect(within(dense!).getByText("Active")).toBeInTheDocument();

    const sparse = (await findRow(/Saadi dealer/)).closest("tr");
    expect(within(sparse!).getAllByText("—").length).toBe(2);
    expect(within(sparse!).getByText("Dealer")).toBeInTheDocument();
    expect(within(sparse!).getByText("Inactive")).toBeInTheDocument();
  });

  it("sends the filter params, debouncing the city", async () => {
    const get = mockReads();
    const user = userEvent.setup();
    renderPage();
    await findRow(/Vanak branch/);

    expect(get.mock.calls[0]).toEqual(["/locations", expect.objectContaining({ params: {} })]);

    await user.click(screen.getByRole("button", { name: /Kind/ }));
    await user.click(await screen.findByRole("option", { name: "Dealer" }, LOAD));
    await waitFor(() => expect(lastParams(get)).toEqual({ kind: "dealer" }), LOAD);
    // The trigger keeps the selected value in its name, like the audit bar.
    expect(screen.getByRole("button", { name: /Dealer/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Status/ }));
    await user.click(await screen.findByRole("option", { name: "Inactive" }, LOAD));
    await waitFor(() => expect(lastParams(get)).toEqual({ kind: "dealer", active: false }), LOAD);

    await user.type(screen.getByRole("textbox", { name: "City" }), "Teh");
    await waitFor(() => expect(lastParams(get)).toEqual({ kind: "dealer", active: false, city: "Teh" }), LOAD);

    await user.clear(screen.getByRole("textbox", { name: "City" }));
    await waitFor(() => expect(lastParams(get)).toEqual({ kind: "dealer", active: false }), LOAD);
  });

  it("blocks an invalid form without sending anything", async () => {
    const post = vi.spyOn(api, "post").mockImplementation(async () => {
      throw new Error("no write may happen");
    });
    mockReads();
    renderPage();
    await findRow(/Vanak branch/);

    fireEvent.click(screen.getByRole("button", { name: "Add location" }));
    const drawer = await screen.findByRole("dialog", { name: "Add location" }, LOAD);
    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));

    expect(await within(drawer).findByText("Enter a name.", undefined, LOAD)).toBeInTheDocument();
    expect(within(drawer).getByText("Enter a code.")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();

    await userEvent.type(within(drawer).getByLabelText("Name"), "North");
    await userEvent.type(within(drawer).getByLabelText("Code"), "north-1");
    await userEvent.type(within(drawer).getByLabelText("Latitude"), "35.7");
    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));

    expect(within(drawer).getByText("Enter both latitude and longitude, or neither.")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("creates a location with the trimmed payload and reports it", async () => {
    const post = vi
      .spyOn(api, "post")
      .mockResolvedValue(okResponse({ ...location({ name: "North" }), warnings: [] }, 201));
    mockReads();
    renderPage();
    await findRow(/Vanak branch/);

    fireEvent.click(screen.getByRole("button", { name: "Add location" }));
    const drawer = await screen.findByRole("dialog", { name: "Add location" }, LOAD);
    const user = userEvent.setup();
    await user.click(within(drawer).getByRole("button", { name: "Kind" }));
    await user.click(await screen.findByRole("option", { name: "Dealer" }, LOAD));
    await userEvent.type(within(drawer).getByLabelText("Name"), "  North  ");
    await userEvent.type(within(drawer).getByLabelText("Code"), " north-1 ");
    await userEvent.type(within(drawer).getByLabelText("City"), "Atlantis");
    await userEvent.type(within(drawer).getByLabelText("Latitude"), "48.85");
    await userEvent.type(within(drawer).getByLabelText("Longitude"), "2.35");
    await userEvent.type(within(drawer).getByLabelText("Service tags"), "oil, wash");
    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));

    await waitFor(
      () =>
        expect(post).toHaveBeenCalledWith("/locations", {
          kind: "dealer",
          name: "North",
          code: "north-1",
          is_active: true,
          city: "Atlantis",
          latitude: 48.85,
          longitude: 2.35,
          service_tags: ["oil", "wash"],
        }),
      LOAD,
    );
    expect(await screen.findByText("Location saved", undefined, LOAD)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(), LOAD);
  });

  it("shows the save warnings and dismisses them", async () => {
    vi.spyOn(api, "post").mockResolvedValue(
      okResponse(
        { ...location({ name: "North" }), warnings: ["coordinates_outside_iran", "city_not_in_gazetteer"] },
        201,
      ),
    );
    mockReads();
    renderPage();
    await findRow(/Vanak branch/);

    fireEvent.click(screen.getByRole("button", { name: "Add location" }));
    const drawer = await screen.findByRole("dialog", { name: "Add location" }, LOAD);
    await userEvent.type(within(drawer).getByLabelText("Name"), "North");
    await userEvent.type(within(drawer).getByLabelText("Code"), "north-1");
    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved “North” with warnings", undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByText("The coordinates are outside Iran.")).toBeInTheDocument();
    expect(screen.getByText("The city is not in the place list. Check the spelling.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(screen.queryByText("The coordinates are outside Iran.")).not.toBeInTheDocument(), LOAD);
  });

  it("renders a repeated warning code twice without React key complaints", async () => {
    const consoleErrors = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(api, "post").mockResolvedValue(
      okResponse({ ...location({ name: "North" }), warnings: ["city_ambiguous", "city_ambiguous"] }, 201),
    );
    mockReads();
    renderPage();
    await findRow(/Vanak branch/);

    fireEvent.click(screen.getByRole("button", { name: "Add location" }));
    const drawer = await screen.findByRole("dialog", { name: "Add location" }, LOAD);
    await userEvent.type(within(drawer).getByLabelText("Name"), "North");
    await userEvent.type(within(drawer).getByLabelText("Code"), "north-1");
    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));

    expect(
      await screen.findAllByText("More than one city has this name. Add the province.", undefined, LOAD),
    ).toHaveLength(2);
    const duplicateKeyWarning = consoleErrors.mock.calls.some(
      ([first]) => typeof first === "string" && /children with the same key/.test(first),
    );
    expect(duplicateKeyWarning).toBe(false);
    consoleErrors.mockRestore();
  });

  it("keeps the drawer open with the typed values on a code_taken", async () => {
    vi.spyOn(api, "post").mockRejectedValue(apiError(409, "code_taken"));
    mockReads();
    renderPage();
    await findRow(/Vanak branch/);

    fireEvent.click(screen.getByRole("button", { name: "Add location" }));
    const drawer = await screen.findByRole("dialog", { name: "Add location" }, LOAD);
    await userEvent.type(within(drawer).getByLabelText("Name"), "North");
    await userEvent.type(within(drawer).getByLabelText("Code"), "vanak");
    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));

    expect(await within(drawer).findByRole("alert", undefined, LOAD)).toHaveTextContent(
      "Another location already uses this code.",
    );
    expect(within(drawer).getByLabelText("Name")).toHaveValue("North");
    expect(within(drawer).getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("edits only the changed fields, clearing to null", async () => {
    const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse(location({ name: "Vanak 2", city: null })));
    mockReads();
    renderPage();
    await findRow(/Vanak branch/);

    fireEvent.click(screen.getByRole("button", { name: "Edit Vanak branch" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit location" }, LOAD);
    expect(within(drawer).getByLabelText("Name")).toHaveValue("Vanak branch");
    expect(within(drawer).getByLabelText("City")).toHaveValue("Tehran");
    expect(within(drawer).getByLabelText("Latitude")).toHaveValue("35.75");

    await userEvent.clear(within(drawer).getByLabelText("Name"));
    await userEvent.type(within(drawer).getByLabelText("Name"), "Vanak 2");
    await userEvent.clear(within(drawer).getByLabelText("City"));
    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patch).toHaveBeenCalledWith("/locations/loc-1", { name: "Vanak 2", city: null }), LOAD);
    expect(await screen.findByText("Location saved", undefined, LOAD)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(), LOAD);
  });

  it("sends nothing and says so when nothing changed", async () => {
    const patch = vi.spyOn(api, "patch").mockImplementation(async () => {
      throw new Error("no write may happen");
    });
    mockReads();
    renderPage();
    await findRow(/Vanak branch/);

    fireEvent.click(screen.getByRole("button", { name: "Edit Vanak branch" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit location" }, LOAD);
    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));

    expect(await within(drawer).findByText("No changes to save.", undefined, LOAD)).toBeInTheDocument();
    expect(patch).not.toHaveBeenCalled();
  });

  it("deletes once through the confirm", async () => {
    const remove = vi.spyOn(api, "delete").mockResolvedValue(okResponse(undefined, 204));
    mockReads();
    renderPage();
    await findRow(/Vanak branch/);

    fireEvent.click(screen.getByRole("button", { name: "Delete Vanak branch" }));
    const dialog = await screen.findByRole("alertdialog", undefined, LOAD);
    expect(within(dialog).getByRole("heading", { name: "Delete location?" })).toBeInTheDocument();
    expect(dialog).toHaveTextContent(/“Vanak branch” \(vanak\) will be removed\. This cannot be undone\./);

    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith("/locations/loc-1"), LOAD);
    expect(remove).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Location deleted", undefined, LOAD)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(), LOAD);
  });

  it("sends nothing when the delete is cancelled", async () => {
    const remove = vi.spyOn(api, "delete").mockImplementation(async () => {
      throw new Error("no write may happen");
    });
    mockReads();
    renderPage();
    await findRow(/Vanak branch/);

    fireEvent.click(screen.getByRole("button", { name: "Delete Vanak branch" }));
    const dialog = await screen.findByRole("alertdialog", undefined, LOAD);
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(), LOAD);
    expect(remove).not.toHaveBeenCalled();
  });

  it("keeps a failed delete in its dialog", async () => {
    vi.spyOn(api, "delete").mockRejectedValue(apiError(400, "delete_refused"));
    mockReads();
    renderPage();
    await findRow(/Vanak branch/);

    fireEvent.click(screen.getByRole("button", { name: "Delete Vanak branch" }));
    const dialog = await screen.findByRole("alertdialog", undefined, LOAD);
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    expect(await within(dialog).findByRole("alert", undefined, LOAD)).toHaveTextContent(
      "The location could not be deleted.",
    );
    expect(dialog).toBeInTheDocument();
  });

  it("returns focus to the Edit button when the edit drawer closes on Cancel", async () => {
    mockReads();
    renderPage();
    await findRow(/Vanak branch/);

    fireEvent.click(screen.getByRole("button", { name: "Edit Vanak branch" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit location" }, LOAD);
    fireEvent.click(within(drawer).getByRole("button", { name: "Cancel" }));

    await waitFor(
      () => expect(screen.getByRole("button", { name: "Edit Vanak branch" })).toHaveFocus(),
      LOAD,
    );
  });

  it("returns focus to the Add location button after cancelling the create drawer", async () => {
    mockReads();
    renderPage();
    await findRow(/Vanak branch/);

    fireEvent.click(screen.getByRole("button", { name: "Add location" }));
    const drawer = await screen.findByRole("dialog", { name: "Add location" }, LOAD);
    fireEvent.click(within(drawer).getByRole("button", { name: "Cancel" }));

    await waitFor(
      () => expect(screen.getByRole("button", { name: "Add location" })).toHaveFocus(),
      LOAD,
    );
  });

  it("returns focus to the Add location button after a successful save", async () => {
    vi.spyOn(api, "post").mockResolvedValue(okResponse({ ...location({ name: "North" }), warnings: [] }, 201));
    mockReads();
    renderPage();
    await findRow(/Vanak branch/);

    fireEvent.click(screen.getByRole("button", { name: "Add location" }));
    const drawer = await screen.findByRole("dialog", { name: "Add location" }, LOAD);
    await userEvent.type(within(drawer).getByLabelText("Name"), "North");
    await userEvent.type(within(drawer).getByLabelText("Code"), "north-1");
    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(), LOAD);
    await waitFor(
      () => expect(screen.getByRole("button", { name: "Add location" })).toHaveFocus(),
      LOAD,
    );
  });

  it("says there are no locations yet, with the first-location hint for managers only", async () => {
    mockReads([]);
    renderPage(workspace("owner"));
    expect(await screen.findByText("No locations yet.", undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByText("Add the first branch, dealer or service center.")).toBeInTheDocument();

    cleanup();
    vi.restoreAllMocks();
    mockReads([]);
    renderPage(workspace("member"));
    expect(await screen.findByText("No locations yet.", undefined, LOAD)).toBeInTheDocument();
    expect(screen.queryByText("Add the first branch, dealer or service center.")).not.toBeInTheDocument();
  });

  it("says no location matches when a filter is set", async () => {
    mockReads([]);
    renderPage();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: /Kind/ }));
    await user.click(await screen.findByRole("option", { name: "Dealer" }, LOAD));
    expect(await screen.findByText("No location matches these filters.", undefined, LOAD)).toBeInTheDocument();
    expect(screen.queryByText("No locations yet.")).not.toBeInTheDocument();
  });

  it("describes a failed list read", async () => {
    vi.spyOn(api, "get").mockRejectedValue(apiError(400, "locations_read_failed"));
    renderPage();
    const alert = await screen.findByRole("alert", undefined, LOAD);
    expect(alert).toHaveTextContent("The locations could not be loaded.");
  });

  it("keeps a workspace without workspace:manage read only", async () => {
    const post = vi.spyOn(api, "post").mockImplementation(async () => {
      throw new Error("no write may happen");
    });
    mockReads();
    renderPage(workspace("owner", ["meeting:read"]));

    await findRow(/Vanak branch/);
    expect(screen.queryByRole("button", { name: "Add location" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit Vanak branch" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete Vanak branch" })).not.toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("mirrors the page in Persian", async () => {
    mockReads();
    renderPage(workspace("owner"), "fa");

    expect(await screen.findByRole("heading", { level: 1, name: "مکان‌های خدمات" }, LOAD)).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
    expect(
      screen.getByText("شعبه‌ها، نمایندگی‌ها و مرکزهای خدمات این فضای کاری."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "افزودن مکان" })).toBeInTheDocument();
    expect(await findRow(/Vanak branch/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ویرایش Vanak branch" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "حذف Vanak branch" })).toBeInTheDocument();
    const persianRow = screen.getByRole("rowheader", { name: /Vanak branch/ }).closest("tr");
    expect(within(persianRow!).getByText("فعال")).toBeInTheDocument();
  });
});
