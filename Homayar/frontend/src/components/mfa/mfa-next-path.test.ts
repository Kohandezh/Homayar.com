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
    ["/x/../mfa/verify", "a resolved dot segment"],
    ["/./mfa/verify", "a leading dot segment"],
    ["/x/%2E%2E/mfa/setup", "an escaped dot segment"],
    ["/x/../login", "a dot segment onto an auth page"],
    ["//mfa/verify", "a protocol-relative path"],
    ["https://example.com/mfa/verify", "an absolute URL"],
    ["/mfa\\verify", "a backslash"],
  ])("refuses %s (%s)", (path) => {
    expect(safeNextPath(path)).toBe("/");
  });

  it.each([
    ["/admin", "/admin"],
    ["/admin/settings", "/admin/settings"],
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

/**
 * HOM-241 review, turn 4: a blocklist of spellings never ends (an escaped
 * `#` or `?` before `..`, a tab or newline the browser strips). The decoded
 * pathname must use only letters, digits, `_` and `-`, in segments joined by
 * single slashes; anything else gives "/".
 */
describe("safeNextPath accepts only plain path segments", () => {
  it.each([
    ["/x%23/../mfa/verify", "an escaped # before a dot segment"],
    ["/x%3F/../mfa/verify", "an escaped ? before a dot segment"],
    ["/m%09fa/verify", "an escaped tab"],
    ["/m%0Afa/verify", "an escaped newline"],
    ["/m\tfa/verify", "a raw tab"],
    ["/m\nfa/verify", "a raw newline"],
    ["/mfa%2Fverify", "an escaped slash"],
    ["/admin/%2E%2E/mfa/setup", "an escaped dot segment after a real page"],
    ["/caf%C3%A9", "a non-ASCII letter"],
    ["/admin%20settings", "an escaped space"],
    ["/admin//settings", "a double slash"],
    ["/admin/./settings", "a dot segment"],
    ["/report.pdf", "a dot in a segment"],
    ["/admin%25", "an escaped percent sign"],
    ["", "an empty value"],
  ])("refuses %j (%s)", (path) => {
    expect(safeNextPath(path)).toBe("/");
  });

  const ID = "3f2b6c1e-8a4d-4f0e-9b7a-2c5d8e1f4a6b";

  it.each([
    "/",
    "/admin",
    "/admin/settings",
    "/admin/api-keys",
    `/meetings/${ID}`,
    `/org/${ID}/audit`,
    `/org/${ID}/scope-grants`,
    "/account/security",
    `/reviews/${ID}`,
    "/invitations/accept",
    "/admin/",
  ])("keeps the real console route %s, with its query and hash", (path) => {
    expect(safeNextPath(path)).toBe(path);
    expect(safeNextPath(`${path}?tab=summary&q=a%20b`)).toBe(`${path}?tab=summary&q=a%20b`);
    expect(safeNextPath(`${path}#s-2`)).toBe(`${path}#s-2`);
    expect(safeNextPath(`${path}?tab=x#top`)).toBe(`${path}?tab=x#top`);
  });

  it("uses the same rule for the sign-in return path", () => {
    expect(safeReturnPath({ from: { pathname: "/x%23/../mfa/verify" } })).toBe("/");
    expect(safeReturnPath({ from: { pathname: "/m%09fa/verify" } })).toBe("/");
    expect(safeReturnPath({ from: { pathname: `/meetings/${ID}`, search: "?tab=summary", hash: "#s-2" } })).toBe(
      `/meetings/${ID}?tab=summary#s-2`,
    );
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
