import type { ReactElement } from "react";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { I18nProvider } from "@/lib/i18n";
import type { Lang } from "@/lib/i18n-context";
import { describeApiError } from "@/lib/error-messages";
import { statusLabel } from "@/lib/status-labels";
import { apiError } from "@/test/fixtures";
import { EmptyState, ErrorState, LoadingState, PageHeader, StatusBadge } from "./Ui";

function renderIn(lang: Lang, ui: ReactElement) {
  if (lang === "en") window.localStorage.setItem("homayar_lang", "en");
  return render(<I18nProvider>{ui}</I18nProvider>);
}

describe("PageHeader", () => {
  it("shows one heading, the eyebrow and the actions", () => {
    renderIn("en", <PageHeader title="Meetings" eyebrow="Workspace" actions={<button type="button">Upload</button>} />);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Meetings");
    expect(screen.getByText("Workspace")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload" })).toBeInTheDocument();
  });

  it("leaves the eyebrow out when it is not given", () => {
    const { container } = renderIn("en", <PageHeader title="Meetings" />);
    expect(container.querySelector("header")).toHaveTextContent(/^Meetings$/);
  });
});

describe("LoadingState", () => {
  it("is one status region with the default label and a HeroUI spinner", () => {
    const { container } = renderIn("en", <LoadingState />);
    const statuses = screen.getAllByRole("status");
    expect(statuses).toHaveLength(1);
    expect(statuses[0]).toHaveTextContent("Loading");
    expect(container.querySelectorAll('[role="status"]')).toHaveLength(1);
    expect(container.querySelector('[data-slot="spinner"]')).not.toBeNull();
  });

  it("uses the given label", () => {
    renderIn("fa", <LoadingState label="در حال بارگذاری" />);
    expect(screen.getByRole("status")).toHaveTextContent("در حال بارگذاری");
  });
});

describe("EmptyState", () => {
  it("shows the label", () => {
    renderIn("en", <EmptyState label="No meetings yet" />);
    expect(screen.getByText("No meetings yet")).toBeInTheDocument();
  });
});

describe("ErrorState", () => {
  it.each<Lang>(["fa", "en"])("shows the localized text in one danger alert (%s)", (lang) => {
    const error = apiError(403, "permission_denied");
    const { container } = renderIn(lang, <ErrorState error={error} fallback="Could not load" />);
    const alerts = screen.getAllByRole("alert");
    expect(alerts).toHaveLength(1);
    expect(container.querySelectorAll('[role="alert"]')).toHaveLength(1);
    expect(alerts[0]).toHaveTextContent(describeApiError(error, { lang, fallback: "Could not load" }).text);
    expect(alerts[0]).not.toHaveTextContent("fixture permission_denied");
    expect(alerts[0].closest('[data-slot="alert-root"]')).toHaveClass("alert--danger");
  });

  it("prefers the page's own code messages", () => {
    const error = apiError(409, "meeting_locked");
    const codes = { meeting_locked: { fa: "جلسه قفل است.", en: "The meeting is locked." } };
    renderIn("en", <ErrorState error={error} codes={codes} />);
    expect(screen.getByRole("alert")).toHaveTextContent("The meeting is locked.");
  });
});

describe("StatusBadge", () => {
  it.each<Lang>(["fa", "en"])("shows the status label for the language (%s)", (lang) => {
    renderIn(lang, <StatusBadge status="completed" />);
    expect(screen.getByText(statusLabel("completed", lang))).toBeInTheDocument();
  });

  it("uses the given label over the status label", () => {
    renderIn("en", <StatusBadge status="owner" label="Owner (you)" />);
    expect(screen.getByText("Owner (you)")).toBeInTheDocument();
  });

  it("keeps the normalized status class on the element that holds the text", () => {
    renderIn("en", <StatusBadge status="DEAD_LETTER" />);
    const text = screen.getByText(statusLabel("DEAD_LETTER", "en"));
    expect(text).toHaveClass("platform-status-dead-letter");
    const chip = text.closest('[data-slot="chip"]');
    expect(chip).not.toBeNull();
    expect(chip).toHaveClass("platform-status", "platform-status-dead-letter");
    expect(within(chip as HTMLElement).getByText(text.textContent ?? "")).toBe(text);
  });

  const groups: Array<[string, string[]]> = [
    ["success", ["completed", "active", "accepted", "success", "done"]],
    ["danger", ["failed", "failure", "dead_letter", "cancelled"]],
    ["warning", ["queued", "uploading", "trialing", "pending", "partial", "insufficient_evidence"]],
    ["accent", ["processing", "transcribing", "diarizing", "analyzing"]],
    ["default", ["owner", "admin", "member", "viewer", "revoked", "expired", "unavailable", "skipped", "something_new"]],
  ];

  it.each(groups)("renders %s chips for their statuses", (color, statuses) => {
    const { container } = renderIn(
      "en",
      <>
        {statuses.map((status) => (
          <StatusBadge key={status} status={status} />
        ))}
      </>,
    );
    const chips = container.querySelectorAll('[data-slot="chip"]');
    expect(chips).toHaveLength(statuses.length);
    chips.forEach((chip) => expect(chip).toHaveClass(`chip--${color}`));
  });
});
