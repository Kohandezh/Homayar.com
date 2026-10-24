// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ContactForm } from "@/components/site/ContactForm";
import en from "@/i18n/en";
import fa from "@/i18n/fa";
import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Copy = Dictionary["contact"]["form"];

const locales: { name: string; copy: Copy; locale: Locale }[] = [
  { name: "en", copy: en.contact.form, locale: "en" },
  { name: "fa", copy: fa.contact.form, locale: "fa" },
];

function installTurnstile() {
  let options: Record<string, unknown> | null = null;
  const calls = { reset: 0 };
  let seq = 0;
  window.turnstile = {
    render(_container: HTMLElement, opts: Record<string, unknown>) {
      options = opts;
      seq += 1;
      return `widget-${seq}`;
    },
    reset() {
      calls.reset += 1;
    },
    remove() {},
  };
  return {
    calls,
    options(): Record<string, unknown> {
      if (!options) throw new Error("turnstile.render was not called yet");
      return options;
    },
    giveToken(token: string) {
      (this.options().callback as (t: string) => void)(token);
    },
    fireError(code: string) {
      (this.options()["error-callback"] as (c: string) => void)(code);
    },
  };
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({ status: 202, json: async () => ({ id: "ref-1" }) });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete window.turnstile;
});

function re(text: string) {
  return new RegExp(`^${text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`);
}

function fillValidForm(copy: Copy) {
  fireEvent.change(screen.getByLabelText(re(copy.name)), { target: { value: "Ada Lovelace" } });
  fireEvent.change(screen.getByLabelText(re(copy.email)), { target: { value: "ada@example.com" } });
  fireEvent.change(screen.getByLabelText(re(copy.company)), { target: { value: "Example Ltd" } });
  fireEvent.change(screen.getByLabelText(re(copy.deployment)), { target: { value: "saas" } });
}

describe("ContactForm waiting state", () => {
  for (const { name, copy, locale } of locales) {
    describe(name, () => {
      it("a field made invalid during the wait leaves waiting, then the kept token sends", async () => {
        const turnstile = installTurnstile();
        render(<ContactForm copy={copy} locale={locale} />);
        await waitFor(() => expect(turnstile.options().callback).toBeTruthy());
        fillValidForm(copy);
        fireEvent.click(screen.getByRole("button", { name: copy.submit }));
        expect(screen.getByText(copy.turnstileWaiting)).toBeTruthy();
        expect((screen.getByRole("button", { name: copy.sending }) as HTMLButtonElement).disabled).toBe(true);
        fireEvent.change(screen.getByLabelText(re(copy.name)), { target: { value: "" } });
        await act(async () => {
          turnstile.giveToken("token-1");
        });
        expect(screen.queryByText(copy.turnstileWaiting)).toBeNull();
        expect((screen.getByRole("button", { name: copy.submit }) as HTMLButtonElement).disabled).toBe(false);
        expect(screen.getAllByText(copy.errors.name).length).toBeGreaterThan(0);
        expect(fetchMock).not.toHaveBeenCalled();
        fireEvent.change(screen.getByLabelText(re(copy.name)), { target: { value: "Ada Lovelace" } });
        fireEvent.click(screen.getByRole("button", { name: copy.submit }));
        await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
        const body = JSON.parse(fetchMock.mock.calls[0][1].body) as { turnstileToken?: string };
        expect(body.turnstileToken).toBe("token-1");
      });

      it("a Turnstile error during the wait leaves waiting with a retry that sends", async () => {
        const turnstile = installTurnstile();
        render(<ContactForm copy={copy} locale={locale} />);
        await waitFor(() => expect(turnstile.options().callback).toBeTruthy());
        fillValidForm(copy);
        fireEvent.click(screen.getByRole("button", { name: copy.submit }));
        expect(screen.getByText(copy.turnstileWaiting)).toBeTruthy();
        await act(async () => {
          turnstile.fireError("110200");
        });
        expect(screen.queryByText(copy.turnstileWaiting)).toBeNull();
        expect((screen.getByRole("button", { name: copy.submit }) as HTMLButtonElement).disabled).toBe(false);
        expect(screen.getByText(copy.turnstileError)).toBeTruthy();
        const retry = screen.getByTestId("contact-turnstile-retry") as HTMLButtonElement;
        expect(retry.textContent).toContain(copy.retry);
        expect(fetchMock).not.toHaveBeenCalled();
        fireEvent.click(retry);
        expect(turnstile.calls.reset).toBe(1);
        expect(screen.getByText(copy.turnstileWaiting)).toBeTruthy();
        await act(async () => {
          turnstile.giveToken("token-1");
        });
        await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
        expect(screen.queryByText(copy.turnstileError)).toBeNull();
      });

      it("a token before Send posts once without waiting (control)", async () => {
        const turnstile = installTurnstile();
        render(<ContactForm copy={copy} locale={locale} />);
        await waitFor(() => expect(turnstile.options().callback).toBeTruthy());
        fillValidForm(copy);
        await act(async () => {
          turnstile.giveToken("token-1");
        });
        fireEvent.click(screen.getByRole("button", { name: copy.submit }));
        await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
        const body = JSON.parse(fetchMock.mock.calls[0][1].body) as { turnstileToken?: string };
        expect(body.turnstileToken).toBe("token-1");
        expect(screen.queryByText(copy.turnstileWaiting)).toBeNull();
      });
    });
  }
});
