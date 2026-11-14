import { Suspense } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router";
import api from "@/lib/api";
import { AppRoutes } from "@/AppRoutes";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { MfaStatus } from "@/types/mfa";

function status(overrides: Partial<MfaStatus> = {}): MfaStatus {
  return { totp_enrolled: false, recovery_codes_remaining: 0, session_verified: false, required: false, ...overrides };
}

const RECOVERY_CODES = [
  "aaaaa-bbbbb", "ccccc-ddddd", "eeeee-fffff", "ggggg-hhhhh", "iiiii-jjjjj",
  "kkkkk-lllll", "mmmmm-nnnnn", "ooooo-ppppp", "qqqqq-rrrrr", "sssss-ttttt",
];

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{`${location.pathname}${location.search}${location.hash}`}</output>;
}

function location() {
  return screen.getByTestId("location").textContent;
}

/** Everything a storage holds, read through the API (stringify of the setup's stub storage would be empty). */
function storageText(storage: Storage): string {
  const parts: string[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (key !== null) parts.push(key, storage.getItem(key) ?? "");
  }
  return parts.join("\n");
}

/** GET /auth/mfa answers one status per call; POST is answered separately. */
function mockReads(answers: (MfaStatus | Error)[]) {
  const urls: string[] = [];
  let index = 0;
  vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    urls.push(url);
    if (url === "/auth/mfa") {
      const answer = answers[Math.min(index, answers.length - 1)];
      index += 1;
      if (answer instanceof Error) throw answer;
      return okResponse(answer);
    }
    throw apiError(404, "not_found");
  });
  return urls;
}

function renderAccount(lang: "fa" | "en" = "en") {
  window.localStorage.removeItem("homayar_lang");
  return renderConsole(
    <Suspense fallback={<p>Loading page</p>}>
      <AppRoutes />
      <LocationProbe />
    </Suspense>,
    {
      route: "/account/security",
      lang,
      auth: {
        status: "authenticated",
        user: FIXTURE_USER,
        workspace: FIXTURE_WORKSPACE,
        workspaces: [FIXTURE_WORKSPACE],
      },
    },
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("account security page (AC7)", () => {
  it("renders inside the layout, for any role, with the sidebar link above Sign out", async () => {
    mockReads([status()]);
    renderAccount();

    expect(await screen.findByRole("heading", { level: 1, name: "Account security" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Main navigation" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Two-step verification" })).toBeInTheDocument();
    // The footer link is above Sign out (AC7) and needs no admin role.
    const footer = screen.getByRole("button", { name: "Sign out" }).closest("div");
    const links = footer ? Array.from(footer.querySelectorAll("a")) : [];
    expect(links.some((link) => link.getAttribute("href") === "/account/security")).toBe(true);
  });

  it("shows Off with the setup link when not enrolled", async () => {
    mockReads([status()]);
    renderAccount();

    expect(await screen.findByText("Off")).toBeInTheDocument();
    expect(screen.queryByText("On")).not.toBeInTheDocument();
    const setup = screen.getByRole("link", { name: "Set up two-step verification" });
    expect(setup).toHaveAttribute("href", "/mfa/setup?next=%2Faccount%2Fsecurity");
    expect(screen.queryByRole("button", { name: "Make new recovery codes" })).not.toBeInTheDocument();
  });

  it("shows the status and the verified actions when enrolled and verified", async () => {
    mockReads([status({ totp_enrolled: true, session_verified: true, required: true, recovery_codes_remaining: 5 })]);
    renderAccount();

    expect(await screen.findByText("On")).toBeInTheDocument();
    expect(screen.getByText("Required for your role")).toBeInTheDocument();
    expect(screen.getByText("5 recovery codes left")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Make new recovery codes" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Use a new authenticator app" })).toHaveAttribute(
      "href",
      "/mfa/setup?next=%2Faccount%2Fsecurity",
    );
    expect(screen.queryByText("This session is not verified yet.")).not.toBeInTheDocument();
  });

  it("asks to verify the session when it is not verified yet", async () => {
    mockReads([status({ totp_enrolled: true, session_verified: false, recovery_codes_remaining: 5 })]);
    renderAccount();

    expect(await screen.findByText("This session is not verified yet.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Verify now" })).toHaveAttribute(
      "href",
      "/mfa/verify?next=%2Faccount%2Fsecurity",
    );
    expect(screen.queryByRole("button", { name: "Make new recovery codes" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Use a new authenticator app" })).not.toBeInTheDocument();
  });

  it("shows the error and Try again when the status read fails, with no request storm (gate and page together)", async () => {
    // Through the real route tree: the gate's read fails, the page's second
    // observer must not refetch it (retryOnMount: false), and "Try again"
    // recovers when the next read succeeds.
    const answers: (MfaStatus | Error)[] = [
      apiError(500, "internal_error"),
      status({ totp_enrolled: true, session_verified: true, recovery_codes_remaining: 5 }),
    ];
    const urls = mockReads(answers);
    const user = userEvent.setup();
    renderAccount();

    expect(await screen.findByRole("heading", { level: 1, name: "Account security" })).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("The service is not available right now; try again shortly.");

    // Exactly the gate's one read; the page's observer adds nothing, and the
    // count stays bounded after a settle wait (the review's request storm).
    const mfaReads = () => urls.filter((url) => url === "/auth/mfa").length;
    expect(mfaReads()).toBe(1);
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(mfaReads()).toBe(1);

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("On")).toBeInTheDocument();
    expect(mfaReads()).toBe(2);
  });

  it("makes new recovery codes through the confirm dialog, and Cancel sends nothing", async () => {
    mockReads([status({ totp_enrolled: true, session_verified: true, recovery_codes_remaining: 1 }), status({ totp_enrolled: true, session_verified: true, recovery_codes_remaining: 10 })]);
    const posts: { url: string }[] = [];
    vi.spyOn(api, "post").mockImplementation(async (url: string) => {
      posts.push({ url });
      if (url === "/auth/mfa/recovery-codes") return okResponse({ recovery_codes: RECOVERY_CODES });
      throw apiError(500, "unexpected_post");
    });
    const user = userEvent.setup();
    renderAccount();

    // Cancel sends nothing.
    await user.click(await screen.findByRole("button", { name: "Make new recovery codes" }));
    const dialog = screen.getByRole("dialog", { name: "Make new recovery codes?" });
    expect(dialog).toHaveTextContent("Your old recovery codes will stop working.");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(posts).toEqual([]);

    // Confirm sends exactly one POST and shows the codes once.
    await user.click(screen.getByRole("button", { name: "Make new recovery codes" }));
    await user.click(screen.getByRole("button", { name: "Make new codes" }));
    expect(await screen.findByRole("heading", { name: "Save your recovery codes" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(10);
    expect(posts).toEqual([{ url: "/auth/mfa/recovery-codes" }]);

    // Continue returns to the account view with the fresh count.
    await user.click(screen.getByRole("checkbox", { name: "I saved my recovery codes" }));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Two-step verification" })).toBeInTheDocument();
    expect(await screen.findByText("10 recovery codes left")).toBeInTheDocument();
  });

  it("keeps the regenerated codes out of storage and the query cache (AC8)", async () => {
    mockReads([status({ totp_enrolled: true, session_verified: true, recovery_codes_remaining: 1 }), status({ totp_enrolled: true, session_verified: true, recovery_codes_remaining: 10 })]);
    vi.spyOn(api, "post").mockImplementation(async (url: string) => {
      if (url === "/auth/mfa/recovery-codes") return okResponse({ recovery_codes: RECOVERY_CODES });
      throw apiError(500, "unexpected_post");
    });
    const user = userEvent.setup();
    const view = renderAccount();
    await user.click(await screen.findByRole("button", { name: "Make new recovery codes" }));
    await user.click(screen.getByRole("button", { name: "Make new codes" }));
    await screen.findByRole("heading", { name: "Save your recovery codes" });

    const surfaces = [
      storageText(window.localStorage),
      storageText(window.sessionStorage),
      JSON.stringify(view.queryClient.getQueryCache().getAll()),
    ];
    for (const surface of surfaces) {
      expect(surface).not.toContain(RECOVERY_CODES[0]);
    }
    expect(location()).not.toContain(RECOVERY_CODES[0]);
  });

  it("goes to the verify page when the regeneration needs a verified session", async () => {
    mockReads([status({ totp_enrolled: true, session_verified: true, recovery_codes_remaining: 5 })]);
    vi.spyOn(api, "post").mockImplementation(async (url: string) => {
      if (url === "/auth/mfa/recovery-codes") throw apiError(403, "mfa_required");
      throw apiError(500, "unexpected_post");
    });
    const user = userEvent.setup();
    renderAccount();

    await user.click(await screen.findByRole("button", { name: "Make new recovery codes" }));
    await user.click(screen.getByRole("button", { name: "Make new codes" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Verify it's you" })).toBeInTheDocument();
    expect(location()).toBe("/mfa/verify?next=%2Faccount%2Fsecurity");
  });
});

describe("account security page in Persian (AC9)", () => {
  it("is right to left with Persian headings and Persian digits", async () => {
    mockReads([status({ totp_enrolled: true, session_verified: true, required: true, recovery_codes_remaining: 7 })]);
    renderAccount("fa");

    expect(await screen.findByRole("heading", { level: 1, name: "امنیت حساب" })).toBeInTheDocument();
    expect(document.documentElement.dir).toBe("rtl");
    expect(screen.getByRole("heading", { level: 2, name: "ورود دومرحله‌ای" })).toBeInTheDocument();
    expect(screen.getByText("فعال")).toBeInTheDocument();
    expect(screen.getByText("برای نقش شما لازم است")).toBeInTheDocument();
    expect(screen.getByText("۷ کد بازیابی باقی مانده است")).toBeInTheDocument();
  });
});
