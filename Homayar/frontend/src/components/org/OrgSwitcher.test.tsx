import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";

import { apiError } from "@/test/fixtures";
import { MAIN_NAV } from "@/lib/navigation";
import {
  LOAD,
  ORG_ID,
  chooseOption,
  location,
  mockOrgReads,
  orgUrl,
  organization,
  renderOrg,
  requestedUrls,
  setupUser,
  summary,
  type OrgReads,
} from "@/components/org/org-test-kit";

function switcher() {
  return screen.queryByRole("button", { name: /Organization/ });
}

async function waitForList(get: ReturnType<typeof mockOrgReads>) {
  await waitFor(() => expect(requestedUrls(get)).toContain("/organizations"), LOAD);
}

describe("organization switcher", { timeout: 60_000 }, () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders nothing while loading, for an empty list, and on error", async () => {
    const answers: OrgReads["list"][] = ["pending", [], apiError(500, "server_error")];
    for (const list of answers) {
      const get = mockOrgReads({ list });
      const view = renderOrg("/meetings");
      await waitForList(get);
      // Give a settled answer time to render, then check nothing appeared.
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(switcher()).not.toBeInTheDocument();
      expect(screen.queryByText("Organization")).not.toBeInTheDocument();
      expect(screen.queryByRole("group", { name: "Organization" })).not.toBeInTheDocument();
      view.unmount();
      vi.restoreAllMocks();
    }
  });

  it("shows one option per organization and navigates on choice", async () => {
    const user = setupUser();
    mockOrgReads({ list: [summary(), summary({ id: "org-2", name: "Beta Holding", role: "analyst" })] });
    renderOrg("/meetings");
    const trigger = await screen.findByRole("button", { name: /Organization/ }, LOAD);
    expect(screen.queryByRole("group", { name: "Organization" })).not.toBeInTheDocument();
    await chooseOption(user, trigger, "Acme Group");
    await waitFor(() => expect(location()).toBe(orgUrl("")), LOAD);
  });

  it("shows the organization links under /org, filtered by the role in the list", async () => {
    mockOrgReads({ list: [summary({ role: "owner" })] });
    const owner = renderOrg(orgUrl("/workspaces"));
    // The lazy page suspends once and remounts the layout, so wait for it first.
    await screen.findByRole("heading", { level: 1, name: "Workspaces" }, LOAD);
    const group = screen.getByRole("group", { name: "Organization" });
    expect(within(group).getAllByRole("link").map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Organization overview", `/org/${ORG_ID}`],
      ["Organization members", `/org/${ORG_ID}/members`],
      ["Workspaces", `/org/${ORG_ID}/workspaces`],
      ["Scope grants", `/org/${ORG_ID}/scope-grants`],
    ]);
    owner.unmount();
    vi.restoreAllMocks();

    mockOrgReads({ list: [summary({ role: "executive" })], detail: organization({ role: "executive" }) });
    renderOrg(orgUrl("/workspaces"));
    await screen.findByRole("heading", { level: 1, name: "Workspaces" }, LOAD);
    const limited = screen.getByRole("group", { name: "Organization" });
    expect(within(limited).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Organization overview",
      "Workspaces",
    ]);
  });

  it("decodes an encoded organization id from the URL, and encodes it once in the links", async () => {
    const id = "org 1";
    mockOrgReads({ id, list: [summary({ id, role: "owner" })], detail: organization({ id }) });
    renderOrg(`/org/${encodeURIComponent(id)}/workspaces`);
    await screen.findByRole("heading", { level: 1, name: "Workspaces" }, LOAD);
    const group = screen.getByRole("group", { name: "Organization" });
    expect(within(group).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([
      "/org/org%201",
      "/org/org%201/members",
      "/org/org%201/workspaces",
      "/org/org%201/scope-grants",
    ]);
    // The switcher shows the current organization as chosen.
    expect(screen.getByRole("button", { name: /Organization/ })).toHaveTextContent("Acme Group");
  });

  it("keeps the existing sidebar entries and their order", async () => {
    mockOrgReads();
    renderOrg(orgUrl(""));
    await screen.findByRole("heading", { level: 1, name: "Acme Group" }, LOAD);
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    const labels = within(nav).getAllByRole("link").map((link) => link.textContent);
    // The switcher leaves the main navigation as MAIN_NAV defines it.
    expect(labels.slice(0, MAIN_NAV.length)).toEqual(MAIN_NAV.map((entry) => entry.label.en));
    const admin = within(screen.getByRole("group", { name: "Administration" })).getAllByRole("link");
    expect(admin[0]).toHaveTextContent("Admin overview");
  });

  it("labels the switcher and the group in Persian", async () => {
    mockOrgReads();
    renderOrg(orgUrl(""), "fa");
    await screen.findByRole("heading", { level: 1, name: "Acme Group" }, LOAD);
    expect(screen.getByRole("button", { name: /سازمان/ })).toBeInTheDocument();
    const group = screen.getByRole("group", { name: "سازمان" });
    expect(within(group).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "نمای کلی سازمان",
      "اعضای سازمان",
      "فضاهای کاری",
      "دسترسی‌های محدود",
    ]);
  });
});
