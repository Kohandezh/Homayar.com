import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";

import api from "@/lib/api";
import { apiError, okResponse } from "@/test/fixtures";
import {
  ALI,
  LOAD,
  ORG_ID,
  OWNER_ROW,
  SARA,
  chooseOption,
  endOfDayIso,
  startOfDayIso,
  typeDay,
  member,
  mockOrgReads,
  optionNames,
  orgUrl,
  organization,
  renderOrg,
  setupUser,
} from "@/components/org/org-test-kit";

const MEMBERS_URL = `/organizations/${ORG_ID}/members`;

function rowOf(name: string) {
  const cell = screen.getByRole("rowheader", { name: new RegExp(name) });
  const row = cell.closest("tr");
  if (!row) throw new Error(`no row for ${name}`);
  return row;
}

async function openPage(role = "owner", members = [OWNER_ROW, ALI, SARA]) {
  const get = mockOrgReads({ detail: organization({ role }), members });
  const view = renderOrg(orgUrl("/members"));
  await screen.findByRole("grid", { name: "Organization members" }, LOAD);
  return { get, view };
}

async function openAddModal() {
  fireEvent.click(screen.getByRole("button", { name: "Add member" }));
  const dialog = await screen.findByRole("dialog", undefined, LOAD);
  expect(within(dialog).getByRole("heading", { name: "Add organization member" })).toBeInTheDocument();
  return dialog;
}

function roleTrigger(dialog: HTMLElement) {
  return within(dialog).getByRole("button", { name: /Role/ });
}

describe("organization members page", { timeout: 60_000 }, () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists members with role, dates and every status in order", async () => {
    const future = new Date(Date.now() + 30 * 86_400_000).toISOString();
    await openPage("owner", [
      OWNER_ROW,
      member({ id: "m-r", user_id: "u-r", display_name: "Revoked One", role: "analyst", revoked_at: "2026-05-01T00:00:00Z", is_active: false, is_effective: false }),
      member({ id: "m-i", user_id: "u-i", display_name: "Inactive One", role: "analyst", is_active: false, is_effective: false }),
      member({ id: "m-s", user_id: "u-s", display_name: "Later One", role: "executive", valid_from: future, is_effective: false }),
      // Stored end: the midnight that starts Feb 2, so Feb 1 is the last included day.
      member({ id: "m-e", user_id: "u-e", display_name: "Old One", role: "executive", email: null, valid_until: endOfDayIso("2026-02-01"), is_effective: false }),
    ]);
    expect(screen.getByRole("heading", { level: 1, name: "Organization members" })).toBeInTheDocument();
    expect(within(rowOf("Person")).getByText("Active")).toBeInTheDocument();
    expect(within(rowOf("Person")).getByText("Owner")).toBeInTheDocument();
    expect(within(rowOf("Person")).getByText("No end date")).toBeInTheDocument();
    expect(within(rowOf("Person")).getByText("person@example.com")).toHaveAttribute("dir", "ltr");
    expect(within(rowOf("Revoked One")).getByText("Revoked")).toBeInTheDocument();
    expect(within(rowOf("Inactive One")).getByText("Inactive")).toBeInTheDocument();
    expect(within(rowOf("Later One")).getByText("Scheduled")).toBeInTheDocument();
    expect(within(rowOf("Old One")).getByText("Expired")).toBeInTheDocument();
    expect(within(rowOf("Old One")).getByText("Executive")).toBeInTheDocument();
    // A null email draws no email line.
    expect(within(rowOf("Old One")).queryByText("person@example.com")).not.toBeInTheDocument();
    expect(within(rowOf("Old One")).getByText("Feb 1, 2026")).toBeInTheDocument();
    expect(within(rowOf("Old One")).queryByText("Feb 2, 2026")).not.toBeInTheDocument();
  });

  it("offers row actions only where the manage rule allows them", async () => {
    const revoked = member({ id: "m-r", user_id: "u-r", display_name: "Gone", role: "analyst", revoked_at: "2026-05-01T00:00:00Z", is_effective: false });
    await openPage("owner", [OWNER_ROW, ALI, SARA, revoked]);
    // The owner manages the analyst and the admin, never themself, never a revoked row.
    expect(screen.getByRole("button", { name: "Edit Ali" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke Ali" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit Sara" })).toBeInTheDocument();
    expect(within(rowOf("Person")).queryAllByRole("button")).toEqual([]);
    expect(within(rowOf("Gone")).queryAllByRole("button")).toEqual([]);
    cleanup();
    vi.restoreAllMocks();

    // An admin cannot change another admin, nor the owner.
    const owner = member({ id: "m-o", user_id: "u-o", display_name: "Boss", role: "owner" });
    const me = member({ id: "m-me", display_name: "Person", role: "admin" });
    await openPage("admin", [owner, me, ALI, SARA]);
    expect(screen.getByRole("button", { name: "Edit Ali" })).toBeInTheDocument();
    expect(within(rowOf("Sara")).queryAllByRole("button")).toEqual([]);
    expect(within(rowOf("Boss")).queryAllByRole("button")).toEqual([]);
    expect(within(rowOf("Person")).queryAllByRole("button")).toEqual([]);
  });

  it("offers the owner admin, analyst and executive, and an admin no Administrator", async () => {
    const user = setupUser();
    await openPage("owner");
    let dialog = await openAddModal();
    expect(await optionNames(user, roleTrigger(dialog))).toEqual(["Administrator", "Analyst", "Executive"]);
    cleanup();
    vi.restoreAllMocks();

    await openPage("admin", [member({ role: "admin" }), ALI]);
    dialog = await openAddModal();
    expect(await optionNames(user, roleTrigger(dialog))).toEqual(["Analyst", "Executive"]);
  });

  it("adds a member with only the fields that are set", async () => {
    const user = setupUser();
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse(member({ id: "om-9" }), 201));
    const { get } = await openPage();
    const dialog = await openAddModal();
    await user.type(within(dialog).getByLabelText("Email"), "  new@example.com  ");
    await chooseOption(user, roleTrigger(dialog), "Executive");
    fireEvent.click(within(dialog).getByRole("button", { name: "Add member" }));

    await waitFor(() => expect(post).toHaveBeenCalledWith(MEMBERS_URL, { email: "new@example.com", role: "executive" }), LOAD);
    expect(await screen.findByText("Member added.", undefined, LOAD)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(), LOAD);
    // The list refreshes.
    await waitFor(() => expect(get.mock.calls.filter(([url]) => url === MEMBERS_URL).length).toBe(2), LOAD);
  });

  it("sends the start day's midnight and the midnight after the included end day", async () => {
    const user = setupUser();
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse(member({ id: "om-9" }), 201));
    await openPage();
    const dialog = await openAddModal();
    await user.type(within(dialog).getByLabelText("Email"), "new@example.com");
    await chooseOption(user, roleTrigger(dialog), "Analyst");
    await typeDay(user, dialog, "Valid from", "2026-11-01");
    await typeDay(user, dialog, "Valid until", "2026-12-01");
    fireEvent.click(within(dialog).getByRole("button", { name: "Add member" }));
    await waitFor(
      () =>
        expect(post).toHaveBeenCalledWith(MEMBERS_URL, {
          email: "new@example.com",
          role: "analyst",
          valid_from: startOfDayIso("2026-11-01"),
          valid_until: endOfDayIso("2026-12-01"),
        }),
      LOAD,
    );
  });

  it("allows one day of access: the same day for both dates", async () => {
    const user = setupUser();
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse(member({ id: "om-9" }), 201));
    await openPage();
    const dialog = await openAddModal();
    await user.type(within(dialog).getByLabelText("Email"), "new@example.com");
    await typeDay(user, dialog, "Valid from", "2026-12-01");
    await typeDay(user, dialog, "Valid until", "2026-12-01");
    expect(within(dialog).queryByText("The end date cannot be before the start date.")).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Add member" }));
    await waitFor(
      () =>
        expect(post).toHaveBeenCalledWith(MEMBERS_URL, {
          email: "new@example.com",
          role: "analyst",
          valid_from: startOfDayIso("2026-12-01"),
          valid_until: endOfDayIso("2026-12-01"),
        }),
      LOAD,
    );
  });

  it("refuses an end day before the start day, and sends nothing", async () => {
    const user = setupUser();
    const post = vi.spyOn(api, "post");
    await openPage();
    const dialog = await openAddModal();
    await user.type(within(dialog).getByLabelText("Email"), "new@example.com");
    await typeDay(user, dialog, "Valid from", "2026-12-02");
    await typeDay(user, dialog, "Valid until", "2026-12-01");
    fireEvent.click(within(dialog).getByRole("button", { name: "Add member" }));
    expect(await within(dialog).findByText("The end date cannot be before the start date.", undefined, LOAD)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("picks days in the Persian calendar on a Persian page and sends the same day", async () => {
    const user = setupUser();
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse(member({ id: "om-9" }), 201));
    mockOrgReads({ members: [OWNER_ROW, ALI] });
    renderOrg(orgUrl("/members"), "fa");
    fireEvent.click(await screen.findByRole("button", { name: "افزودن عضو" }, LOAD));
    const dialog = await screen.findByRole("dialog", undefined, LOAD);
    await user.type(within(dialog).getByLabelText("ایمیل"), "new@example.com");
    const from = within(dialog).getByRole("group", { name: "معتبر از" });
    // Persian order: year, month, day. 1405/08/10 is 2026-11-01.
    await user.click(within(from).getAllByRole("spinbutton")[0]);
    await user.keyboard("14050810");
    fireEvent.click(within(dialog).getByRole("button", { name: "افزودن عضو" }));
    await waitFor(
      () =>
        expect(post).toHaveBeenCalledWith(MEMBERS_URL, {
          email: "new@example.com",
          role: "analyst",
          valid_from: startOfDayIso("2026-11-01"),
        }),
      LOAD,
    );
  });

  it.each([
    ["invalid_user", 422, "No verified account uses this email."],
    ["organization_member_exists", 409, "This person is already a member of the organization."],
    ["organization_grant_denied", 403, "Your role cannot grant this role."],
    ["invalid_validity_window", 422, "The end date must be after the start date."],
  ] as const)("keeps the modal open with the text of %s", async (code, status, text) => {
    const user = setupUser();
    vi.spyOn(api, "post").mockRejectedValue(apiError(status, code));
    await openPage();
    const dialog = await openAddModal();
    await user.type(within(dialog).getByLabelText("Email"), "new@example.com");
    fireEvent.click(within(dialog).getByRole("button", { name: "Add member" }));
    expect(await within(dialog).findByRole("alert", undefined, LOAD)).toHaveTextContent(text);
    expect(within(dialog).getByLabelText("Email")).toHaveValue("new@example.com");
  });

  it("patches only a changed role, and keeps save off without a change", async () => {
    const user = setupUser();
    const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse(ALI));
    await openPage();
    fireEvent.click(screen.getByRole("button", { name: "Edit Ali" }));
    const dialog = await screen.findByRole("dialog", undefined, LOAD);
    expect(within(dialog).getByRole("heading", { name: "Edit membership" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(within(dialog).getByRole("checkbox", { name: "No end date" })).toBeChecked();

    await chooseOption(user, within(dialog).getByRole("button", { name: /Role/ }), "Executive");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith(`${MEMBERS_URL}/om-2`, { role: "executive" }), LOAD);
    expect(await screen.findByText("Membership updated.", undefined, LOAD)).toBeInTheDocument();
  });

  it("patches only a new end date, or only clear_valid_until when the end date is removed", async () => {
    const user = setupUser();
    const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse(ALI));
    await openPage();
    fireEvent.click(screen.getByRole("button", { name: "Edit Ali" }));
    let dialog = await screen.findByRole("dialog", undefined, LOAD);
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "No end date" }));
    // Unticked but no date yet: still nothing to save.
    expect(within(dialog).getByRole("button", { name: "Save changes" })).toBeDisabled();
    await typeDay(user, dialog, "Valid until", "2027-03-01");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    // The picked day is included: the end is the midnight after it.
    await waitFor(
      () => expect(patch).toHaveBeenCalledWith(`${MEMBERS_URL}/om-2`, { valid_until: endOfDayIso("2027-03-01") }),
      LOAD,
    );
    cleanup();
    vi.restoreAllMocks();

    const ending = member({ ...ALI, valid_until: endOfDayIso("2027-03-01") });
    const clear = vi.spyOn(api, "patch").mockResolvedValue(okResponse(ending));
    await openPage("owner", [OWNER_ROW, ending]);
    fireEvent.click(screen.getByRole("button", { name: "Edit Ali" }));
    dialog = await screen.findByRole("dialog", undefined, LOAD);
    expect(within(dialog).getByRole("checkbox", { name: "No end date" })).not.toBeChecked();
    // The prefill is the last included day, not the stored midnight after it.
    const [month, day, year] = within(within(dialog).getByRole("group", { name: "Valid until" })).getAllByRole("spinbutton");
    expect([month, day, year].map((segment) => segment.getAttribute("aria-valuenow"))).toEqual(["3", "1", "2027"]);
    expect(within(dialog).getByRole("button", { name: "Save changes" })).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "No end date" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(clear).toHaveBeenCalledWith(`${MEMBERS_URL}/om-2`, { clear_valid_until: true }), LOAD);
  });

  it("puts the stored end day back when No end date is ticked and unticked, so nothing is sent", async () => {
    const patch = vi.spyOn(api, "patch");
    const ending = member({ ...ALI, valid_until: endOfDayIso("2027-03-01") });
    await openPage("owner", [OWNER_ROW, ending]);
    fireEvent.click(screen.getByRole("button", { name: "Edit Ali" }));
    const dialog = await screen.findByRole("dialog", undefined, LOAD);
    const box = within(dialog).getByRole("checkbox", { name: "No end date" });
    fireEvent.click(box);
    expect(box).toBeChecked();
    fireEvent.click(box);
    expect(box).not.toBeChecked();
    // The draft is the original again: the last included day, and nothing to save.
    const segments = within(within(dialog).getByRole("group", { name: "Valid until" })).getAllByRole("spinbutton");
    expect(segments.map((segment) => segment.getAttribute("aria-valuenow"))).toEqual(["3", "1", "2027"]);
    const save = within(dialog).getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(patch).not.toHaveBeenCalled();
  });

  it("keeps Save off when the date is cleared by keyboard with No end date unticked", async () => {
    const user = setupUser();
    const patch = vi.spyOn(api, "patch");
    const ending = member({ ...ALI, valid_until: endOfDayIso("2027-03-01") });
    await openPage("owner", [OWNER_ROW, ending]);
    fireEvent.click(screen.getByRole("button", { name: "Edit Ali" }));
    const dialog = await screen.findByRole("dialog", undefined, LOAD);
    // A role change too, so only the empty date can be what keeps Save off.
    await chooseOption(user, within(dialog).getByRole("button", { name: /Role/ }), "Executive");
    const segments = within(within(dialog).getByRole("group", { name: "Valid until" })).getAllByRole("spinbutton");
    await user.click(segments[2]);
    await user.keyboard("{Backspace>12/}");
    for (const segment of segments) expect(segment).not.toHaveAttribute("aria-valuenow");
    expect(within(dialog).getByRole("checkbox", { name: "No end date" })).not.toBeChecked();
    const save = within(dialog).getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();
    fireEvent.submit(save.closest("form")!);
    expect(patch).not.toHaveBeenCalled();
  });

  it("still clears the end date when No end date is ticked and saved", async () => {
    const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse(ALI));
    const ending = member({ ...ALI, valid_until: endOfDayIso("2027-03-01") });
    await openPage("owner", [OWNER_ROW, ending]);
    fireEvent.click(screen.getByRole("button", { name: "Edit Ali" }));
    const dialog = await screen.findByRole("dialog", undefined, LOAD);
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "No end date" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith(`${MEMBERS_URL}/om-2`, { clear_valid_until: true }), LOAD);
  });

  it("revokes a membership after the confirmation", async () => {
    const remove = vi.spyOn(api, "delete").mockResolvedValue(okResponse(undefined, 204));
    await openPage();
    fireEvent.click(screen.getByRole("button", { name: "Revoke Ali" }));
    const dialog = await screen.findByRole("alertdialog", undefined, LOAD);
    expect(within(dialog).getByRole("heading", { name: "Revoke membership?" })).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Ali loses access to this organization now.");
    // Only the name carries dir="auto", never the sentence.
    expect(within(dialog).getByText("Ali")).toHaveAttribute("dir", "auto");
    expect(within(dialog).getByText("Ali").parentElement).not.toHaveAttribute("dir");
    fireEvent.click(within(dialog).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith(`${MEMBERS_URL}/om-2`), LOAD);
    expect(await screen.findByText("Membership revoked.", undefined, LOAD)).toBeInTheDocument();
  });

  it("renders right to left in Persian with Persian copy", async () => {
    mockOrgReads({ members: [OWNER_ROW, ALI] });
    renderOrg(orgUrl("/members"), "fa");
    expect(await screen.findByRole("heading", { level: 1, name: "اعضای سازمان" }, LOAD)).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
    expect(await screen.findByRole("grid", { name: "اعضای سازمان" }, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "افزودن عضو" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ویرایش Ali" })).toBeInTheDocument();
    expect(screen.getAllByText("بدون تاریخ پایان").length).toBeGreaterThan(0);
    expect(screen.getByText("تحلیلگر")).toBeInTheDocument();
  });
});
