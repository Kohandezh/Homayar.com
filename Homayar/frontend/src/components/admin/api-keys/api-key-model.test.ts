import { describe, expect, it } from "vitest";
import {
  API_KEY_SCOPES,
  DEFAULT_EXPIRY_DAYS,
  EXPIRY_CHOICES,
  apiDocsUrl,
  apiKeyStatus,
  createPostBody,
  creatorLabel,
  expiryWarningDays,
  expiryWarningLabel,
  orderedScopes,
} from "@/components/admin/api-keys/api-key-model";
import type { ApiKey, WorkspaceMember } from "@/types/platform";

// Copy of backend/security/authorization.py:143-153 (API_KEY_SCOPES), written
// out by hand so a drift on either side fails here, the same way
// src/lib/roles.test.ts copies the role matrix.
const BACKEND_API_KEY_SCOPES = [
  "meeting:read",
  "meeting:create",
  "meeting:update",
  "member:read",
  "team:read",
  "billing:read",
  "audit:read",
];

const NOW = Date.parse("2026-10-18T12:00:00Z");

/** A timestamp `days` days from NOW, to the second. */
function inDays(days: number, seconds = 0): string {
  return new Date(NOW + days * 86_400_000 + seconds * 1000).toISOString();
}

function key(overrides: Partial<Pick<ApiKey, "revoked_at" | "expires_at">> = {}): Pick<ApiKey, "revoked_at" | "expires_at"> {
  return { revoked_at: null, expires_at: null, ...overrides };
}

describe("API_KEY_SCOPES", () => {
  it("matches the backend's API_KEY_SCOPES exactly, in order", () => {
    expect(API_KEY_SCOPES.map((entry) => entry.scope)).toEqual(BACKEND_API_KEY_SCOPES);
  });

  it("has an en and a fa line for every scope", () => {
    for (const entry of API_KEY_SCOPES) {
      expect(entry.en.trim().length).toBeGreaterThan(0);
      expect(entry.fa.trim().length).toBeGreaterThan(0);
    }
  });
});

describe("expiry choices", () => {
  it("offers 30, 90 (default), 180 and 365 days", () => {
    expect(EXPIRY_CHOICES.map((choice) => choice.days)).toEqual([30, 90, 180, 365]);
    expect(DEFAULT_EXPIRY_DAYS).toBe(90);
    // The server's api_key_max_ttl_days is 365; the console never offers more.
    expect(Math.max(...EXPIRY_CHOICES.map((choice) => choice.days))).toBeLessThanOrEqual(365);
  });
});

describe("apiKeyStatus", () => {
  it("is active without an expiry, and with one in the future", () => {
    expect(apiKeyStatus(key(), NOW)).toBe("active");
    expect(apiKeyStatus(key({ expires_at: inDays(90) }), NOW)).toBe("active");
  });

  it("is expired the moment expires_at equals now", () => {
    expect(apiKeyStatus(key({ expires_at: new Date(NOW).toISOString() }), NOW)).toBe("expired");
    expect(apiKeyStatus(key({ expires_at: inDays(-1) }), NOW)).toBe("expired");
  });

  it("lets revoked win over expired", () => {
    expect(
      apiKeyStatus(key({ expires_at: inDays(-30), revoked_at: inDays(-1) }), NOW),
    ).toBe("revoked");
  });
});

describe("expiryWarningDays", () => {
  it("warns while 14 or fewer whole days remain, rounded up", () => {
    expect(expiryWarningDays(key({ expires_at: inDays(13) }), NOW)).toBe(13);
    expect(expiryWarningDays(key({ expires_at: inDays(14) }), NOW)).toBe(14);
    // 36 hours is two days by rounding up, so it still warns.
    expect(expiryWarningDays(key({ expires_at: inDays(1, 3600 * 12) }), NOW)).toBe(2);
  });

  it("does not warn when more than 14 days remain, or there is no expiry", () => {
    expect(expiryWarningDays(key({ expires_at: inDays(15) }), NOW)).toBeNull();
    expect(expiryWarningDays(key({ expires_at: inDays(90) }), NOW)).toBeNull();
    expect(expiryWarningDays(key(), NOW)).toBeNull();
  });

  it("never warns on an expired or revoked key", () => {
    expect(expiryWarningDays(key({ expires_at: inDays(-1) }), NOW)).toBeNull();
    expect(expiryWarningDays(key({ expires_at: inDays(5), revoked_at: inDays(-1) }), NOW)).toBeNull();
  });
});

describe("expiryWarningLabel", () => {
  it("uses the singular only for exactly one day", () => {
    expect(expiryWarningLabel(1, "en")).toBe("Expires in 1 day");
    expect(expiryWarningLabel(2, "en")).toBe("Expires in 2 days");
  });

  it("uses Persian digits in Persian", () => {
    expect(expiryWarningLabel(13, "fa")).toBe("۱۳ روز تا انقضا");
    expect(expiryWarningLabel(1, "fa")).toBe("۱ روز تا انقضا");
  });
});

describe("orderedScopes", () => {
  it("orders known scopes by the K4 order and keeps unknown ones last, in server order", () => {
    expect(orderedScopes(["team:read", "meeting:create", "audit:read"])).toEqual([
      "meeting:create",
      "team:read",
      "audit:read",
    ]);
    expect(orderedScopes(["zeta:all", "meeting:read", "alpha:all"])).toEqual([
      "meeting:read",
      "zeta:all",
      "alpha:all",
    ]);
  });
});

describe("createPostBody", () => {
  it("trims the name and sends scopes in K4 order with the chosen days", () => {
    expect(
      createPostBody("  Office PBX  ", ["team:read", "meeting:read", "meeting:create"], 30),
    ).toEqual({ name: "Office PBX", scopes: ["meeting:read", "meeting:create", "team:read"], expires_in_days: 30 });
  });
});

describe("creatorLabel", () => {
  const base: Pick<WorkspaceMember, "user_id" | "display_name" | "email" | "phone_e164"> = {
    user_id: "user-1",
    display_name: "سارا احمدی",
    email: "owner@demo.example.com",
    phone_e164: null,
  };

  it("shows the display name", () => {
    expect(creatorLabel(base)).toBe("سارا احمدی");
  });

  it("falls back to the email, then the phone, then the user id (A1)", () => {
    expect(creatorLabel({ ...base, display_name: "  " })).toBe("owner@demo.example.com");
    expect(creatorLabel({ ...base, display_name: "", email: null, phone_e164: "+989120000000" })).toBe("+989120000000");
    // A matching member never shows "Former member"; the last resort is the id.
    expect(creatorLabel({ ...base, display_name: "", email: null, phone_e164: null })).toBe("user-1");
  });
});

describe("apiDocsUrl", () => {
  it("is exactly /api/docs for the default base", () => {
    expect(apiDocsUrl("/api/v1")).toBe("/api/docs");
  });

  it("keeps another server's origin", () => {
    expect(apiDocsUrl("https://api.example.com/api/v1")).toBe("https://api.example.com/api/docs");
  });
});
