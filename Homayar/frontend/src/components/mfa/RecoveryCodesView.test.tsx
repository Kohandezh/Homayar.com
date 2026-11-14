import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RecoveryCodesView from "@/components/mfa/RecoveryCodesView";
import { renderConsole } from "@/test/fixtures";

const CODES = [
  "aaaaa-bbbbb", "ccccc-ddddd", "eeeee-fffff", "ggggg-hhhhh", "iiiii-jjjjj",
  "kkkkk-lllll", "mmmmm-nnnnn", "ooooo-ppppp", "qqqqq-rrrrr", "sssss-ttttt",
];

function stubClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(window.navigator, "clipboard", { value: { writeText }, configurable: true });
  return writeText;
}

afterEach(() => {
  vi.restoreAllMocks();
  Reflect.deleteProperty(window.navigator, "clipboard");
});

describe("recovery codes view (AC6)", () => {
  it("lists the ten codes once, left to right", () => {
    renderConsole(<RecoveryCodesView codes={CODES} onContinue={vi.fn()} />, { lang: "en" });

    expect(screen.getByRole("heading", { name: "Save your recovery codes" })).toBeInTheDocument();
    expect(
      screen.getByText("Each code works once. You can use one if you lose your phone. This is the only time they are shown."),
    ).toBeInTheDocument();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(10);
    expect(items[0]).toHaveTextContent(CODES[0]);
    expect(items[0].closest("ol")).toHaveAttribute("dir", "ltr");
  });

  it("copies all codes, one per line", async () => {
    const user = userEvent.setup();
    // After setup: user-event would otherwise install its own clipboard stub.
    const writeText = stubClipboard();
    renderConsole(<RecoveryCodesView codes={CODES} onContinue={vi.fn()} />, { lang: "en" });
    await user.click(screen.getByRole("button", { name: "Copy codes" }));
    expect(writeText).toHaveBeenCalledWith(CODES.join("\n"));
  });

  it("keeps Continue disabled until the saved checkbox is on", async () => {
    const onContinue = vi.fn();
    const user = userEvent.setup();
    renderConsole(<RecoveryCodesView codes={CODES} onContinue={onContinue} />, { lang: "en" });

    const button = screen.getByRole("button", { name: "Continue" });
    expect(button).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "I saved my recovery codes" }));
    expect(button).toBeEnabled();
    await user.click(button);
    expect(onContinue).toHaveBeenCalledTimes(1);
  });
});
