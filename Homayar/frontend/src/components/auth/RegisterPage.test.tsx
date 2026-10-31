import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router";
import api from "@/lib/api";
import RegisterPage from "@/components/auth/RegisterPage";
import { apiError, okResponse, renderConsole, withAuthMethods } from "@/test/fixtures";

const TOKEN = "i".repeat(43);

function LocationProbe() {
  const location = useLocation();
  return <p data-testid="location-probe">{JSON.stringify(location.state)}</p>;
}

function renderRegister(options: Parameters<typeof renderConsole>[1] = {}) {
  return renderConsole(
    <Routes>
      <Route path="register" element={<RegisterPage />} />
      <Route path="login" element={<LocationProbe />} />
    </Routes>,
    { route: "/register", ...options },
  );
}

describe("RegisterPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a closed card instead of the form when sign-up is closed", () => {
    renderRegister({ lang: "en", queryClient: withAuthMethods({ registration: false }) });

    expect(screen.getByRole("heading", { name: "Sign-up is closed" })).toBeInTheDocument();
    expect(screen.getByText(/invitation only/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Full name")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Work email")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create account" })).not.toBeInTheDocument();
  });

  it("shows the closed card in Persian too", () => {
    renderRegister({ queryClient: withAuthMethods({ registration: false }) });

    expect(screen.getByRole("heading", { name: "ثبت‌نام بسته است" })).toBeInTheDocument();
    expect(screen.getByText(/فقط با دعوت‌نامه/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "وارد شوید" })).toBeInTheDocument();
    expect(screen.queryByLabelText("نام و نام خانوادگی")).not.toBeInTheDocument();
  });

  it("ignores an invitation parameter that is not a plausible token", () => {
    renderRegister({
      lang: "en",
      route: "/register?invitation=short",
      queryClient: withAuthMethods({ registration: false }),
    });

    expect(screen.getByRole("heading", { name: "Sign-up is closed" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Full name")).not.toBeInTheDocument();
  });

  it("shows the form for an invitee while sign-up is closed and sends the token", async () => {
    const user = userEvent.setup();
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse({}, 202));
    renderRegister({
      lang: "en",
      route: `/register?invitation=${TOKEN}`,
      queryClient: withAuthMethods({ registration: false }),
    });

    await user.type(await screen.findByLabelText("Full name"), "Invited Person");
    await user.type(screen.getByLabelText("Work email"), "invited@example.com");
    await user.type(screen.getByLabelText("Password"), "strong-password1");
    await user.type(screen.getByLabelText("Confirm password"), "strong-password1");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(post).toHaveBeenCalledWith("/auth/register", {
      display_name: "Invited Person",
      email: "invited@example.com",
      password: "strong-password1",
      locale: "en",
      invitation_token: TOKEN,
    });
  }, 15_000);

  it("sends the invitation token when sign-up is open too", async () => {
    const user = userEvent.setup();
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse({}, 202));
    renderRegister({
      lang: "en",
      route: `/register?invitation=${TOKEN}`,
      queryClient: withAuthMethods({}),
    });

    await user.type(await screen.findByLabelText("Full name"), "Invited Person");
    await user.type(screen.getByLabelText("Work email"), "invited@example.com");
    await user.type(screen.getByLabelText("Password"), "strong-password1");
    await user.type(screen.getByLabelText("Confirm password"), "strong-password1");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(post).toHaveBeenCalledWith(
      "/auth/register",
      expect.objectContaining({ invitation_token: TOKEN }),
    );
  }, 15_000);

  it("keeps the register payload unchanged without an invitation parameter", async () => {
    const user = userEvent.setup();
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse({}, 202));
    renderRegister({ lang: "en", queryClient: withAuthMethods({}) });

    await user.type(await screen.findByLabelText("Full name"), "Person");
    await user.type(screen.getByLabelText("Work email"), "person@example.com");
    await user.type(screen.getByLabelText("Password"), "strong-password1");
    await user.type(screen.getByLabelText("Confirm password"), "strong-password1");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(post).toHaveBeenCalledWith("/auth/register", {
      display_name: "Person",
      email: "person@example.com",
      password: "strong-password1",
      locale: "en",
    });
  }, 15_000);

  it("explains a registration_closed refusal in the form", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "post").mockRejectedValue(apiError(403, "registration_closed"));
    renderRegister({
      lang: "en",
      route: `/register?invitation=${TOKEN}`,
      queryClient: withAuthMethods({ registration: false }),
    });

    await user.type(await screen.findByLabelText("Full name"), "Invited Person");
    await user.type(screen.getByLabelText("Work email"), "invited@example.com");
    await user.type(screen.getByLabelText("Password"), "strong-password1");
    await user.type(screen.getByLabelText("Confirm password"), "strong-password1");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Sign-up is closed");
  }, 15_000);

  it("sends an invited newcomer back to the invitation page after signing in", async () => {
    const user = userEvent.setup();
    // A "=" must be percent-encoded on the way out, like the invitation page does.
    const tokenNeedingEncoding = `i${"=".repeat(10)}3`.repeat(4);
    vi.spyOn(api, "post").mockResolvedValue(okResponse({}, 202));
    renderRegister({
      lang: "en",
      route: `/register?invitation=${tokenNeedingEncoding}`,
      queryClient: withAuthMethods({ registration: false }),
    });

    await user.type(await screen.findByLabelText("Full name"), "Invited Person");
    await user.type(screen.getByLabelText("Work email"), "invited@example.com");
    await user.type(screen.getByLabelText("Password"), "strong-password1");
    await user.type(screen.getByLabelText("Confirm password"), "strong-password1");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    await user.click(await screen.findByRole("link", { name: "Continue to sign in" }));
    const state = JSON.parse(screen.getByTestId("location-probe").textContent ?? "null");
    expect(state).toEqual({
      from: {
        pathname: "/invitations/accept",
        search: `?token=${encodeURIComponent(tokenNeedingEncoding)}`,
      },
    });
  }, 15_000);

  it("waits for the server answer before showing anything", () => {
    vi.spyOn(api, "get").mockImplementation(() => new Promise(() => {}));
    renderRegister({ lang: "en" });

    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByLabelText("Full name")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Sign-up is closed" }),
    ).not.toBeInTheDocument();
  });
});
