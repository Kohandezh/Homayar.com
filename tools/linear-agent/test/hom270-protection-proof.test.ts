import { describe, expect, it } from "vitest";

describe("HOM-270 branch protection proof", () => {
  it("fails on purpose so this throwaway PR has a red required check", () => {
    expect("red").toBe("green");
  });
});
