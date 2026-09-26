import { describe, expect, it } from "vitest";
import { describeApiError } from "@/lib/error-messages";
import { apiError, networkError } from "@/test/fixtures";

describe("describeApiError (QA X-01)", () => {
  const codes = { member_conflict: { fa: "این ایمیل از قبل عضو است.", en: "Already a member." } };

  it("prefers the page's text for the code, then the common text, and never the server message", () => {
    expect(describeApiError(apiError(409, "member_conflict"), { lang: "fa", codes }).text).toBe("این ایمیل از قبل عضو است.");
    const common = describeApiError(apiError(403, "permission_denied"), { lang: "en", codes });
    expect(common.text).toBe("Your access does not allow this, or it has changed.");
    expect(common.kind).toBe("authorization");
    expect(common.text).not.toContain("fixture");
  });

  it("distinguishes network, service, rate-limit and unknown failures", () => {
    expect(describeApiError(networkError(), { lang: "en" }).text).toMatch(/server could not be reached/);
    expect(describeApiError(apiError(502, "bad_gateway"), { lang: "en" }).kind).toBe("unavailable");
    expect(describeApiError(apiError(429, "upload_rate_limited", { headers: { "Retry-After": "120" } }), { lang: "fa" }).text)
      .toBe("درخواست‌ها زیاد بوده است؛ ۲ دقیقهٔ دیگر دوباره تلاش کنید.");
    const unknown = describeApiError(apiError(409, "something_new"), { lang: "en", fallback: "The action failed." });
    expect(unknown.text).toBe("The action failed. (Reference: req-fixture)");
  });

  it("uses the fallback for a non-API error without inventing detail", () => {
    expect(describeApiError(new Error("boom"), { lang: "en", fallback: "No workspace." }).text).toBe("No workspace.");
  });
});
