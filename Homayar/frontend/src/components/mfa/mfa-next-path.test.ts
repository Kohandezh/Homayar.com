import { describe, expect, it } from "vitest";
import { isMfaPathname, safeNextPath, safeReturnPath } from "@/lib/return-path";

/**
 * The `next` and return-path checks must read the pathname the way the
 * router matches it: percent escapes decoded, case ignored (HOM-241 review,
 * turn 2 item 2). `/MFA/verify` or `/%6Dfa/verify` would otherwise pass as a
 * `next` and land the user on an MFA page again after a correct code.
 */
describe("safeNextPath refuses MFA and auth pages however they are spelled", () => {
  it.each([
    ["/mfa/verify", "the plain path"],
    ["/MFA/verify", "upper case"],
    ["/Mfa/Setup", "mixed case"],
    ["/%6Dfa/verify", "an escaped m"],
    ["/mfa", "the bare prefix"],
    ["/MFA", "the bare prefix, upper case"],
    ["/login", "an auth page"],
    ["/LOGIN", "an auth page, upper case"],
    ["/Login/Forgot", "an auth page child, mixed case"],
    ["/%6Cogin", "an auth page, escaped"],
    ["/%E0%A4%A", "a broken escape"],
    ["//mfa/verify", "a protocol-relative path"],
    ["https://example.com/mfa/verify", "an absolute URL"],
    ["/mfa\\verify", "a backslash"],
  ])("refuses %s (%s)", (path) => {
    expect(safeNextPath(path)).toBe("/");
  });

  it.each([
    ["/admin", "/admin"],
    ["/Meetings", "/Meetings"],
    ["/account/security", "/account/security"],
    ["/admin/teams?tab=x#top", "/admin/teams?tab=x#top"],
    // Not an MFA page: the prefix check is on path segments, not substrings.
    ["/mfaq", "/mfaq"],
  ])("keeps the internal path %s", (path, expected) => {
    expect(safeNextPath(path)).toBe(expected);
  });

  it("refuses an MFA page as the sign-in return path too", () => {
    expect(safeReturnPath({ from: { pathname: "/MFA/verify" } })).toBe("/");
    expect(safeReturnPath({ from: { pathname: "/%6Dfa/setup" } })).toBe("/");
  });
});

describe("isMfaPathname (the listener's no-loop check)", () => {
  it.each([
    "/mfa",
    "/mfa/setup",
    "/MFA/verify",
    "/%6Dfa/verify",
    "/Mfa/",
  ])("is true for %s", (path) => {
    expect(isMfaPathname(path)).toBe(true);
  });

  it.each([
    "/admin",
    "/mfaq",
    "/mfaq/verify",
    "/%E0%A4%A",
    "",
  ])("is false for %s", (path) => {
    expect(isMfaPathname(path)).toBe(false);
  });
});
