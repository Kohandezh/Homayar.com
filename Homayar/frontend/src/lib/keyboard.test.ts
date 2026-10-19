import { describe, expect, it } from "vitest";
import { shortcutTargetBlocked } from "@/lib/keyboard";

function targetOf(html: string): EventTarget | null {
  const container = document.createElement("div");
  container.innerHTML = html;
  document.body.append(container);
  const target = container.querySelector("[data-target]") ?? container.firstElementChild;
  return target;
}

describe("shortcutTargetBlocked", () => {
  it.each([
    ["<input data-target value='' />"],
    ["<textarea data-target></textarea>"],
    ["<select data-target><option>a</option></select>"],
    ['<div contenteditable="true" data-target>text</div>'],
    ['<div contenteditable="plaintext-only" data-target>text</div>'],
    ['<div role="dialog" ><input data-target /></div>'],
    ['<div role="alertdialog"><button data-target>x</button></div>'],
    ["<form><input data-target /></form>"],
  ])("blocks a key from %s", (html) => {
    expect(shortcutTargetBlocked(targetOf(html))).toBe(true);
  });

  it("lets a key through on the page body and plain controls", () => {
    expect(shortcutTargetBlocked(document.body)).toBe(false);
    expect(shortcutTargetBlocked(targetOf("<button data-target>Play</button>"))).toBe(false);
    expect(shortcutTargetBlocked(targetOf("<p data-target>text</p>"))).toBe(false);
    expect(shortcutTargetBlocked(null)).toBe(false);
    expect(shortcutTargetBlocked(targetOf('<div contenteditable="false" data-target>text</div>'))).toBe(false);
  });
});
