import { describe, expect, it } from "vitest";
import { AUDIT_EVENT_LABELS, auditEventLabel } from "@/lib/audit-labels";

describe("audit event labels", () => {
  it("labels the derivative and inherited-hold media events (HOM-314)", () => {
    expect(auditEventLabel("media.derivative_unavailable", "fa")).toBe(
      "نسخهٔ پردازش‌شدهٔ فایل رسانه ساخته نشد",
    );
    expect(auditEventLabel("media.derivative_unavailable", "en")).toBe(
      "Media derivative not created",
    );
    expect(auditEventLabel("media.legal_hold_inherited", "fa")).toBe(
      "نگهداری قانونی از فایل مبدأ به ارث رسید",
    );
    expect(auditEventLabel("media.legal_hold_inherited", "en")).toBe("Legal hold inherited");
  });

  it("labels the kept-hold media event (HOM-329)", () => {
    expect(auditEventLabel("media.legal_hold_kept", "fa")).toBe("نگهداری قانونی حفظ شد");
    expect(auditEventLabel("media.legal_hold_kept", "en")).toBe("Legal hold kept");
  });

  it("keeps the existing media labels and shows an unknown code as unlabelled", () => {
    expect(auditEventLabel("media.legal_hold_placed", "en")).toBe("Legal hold placed");
    expect(auditEventLabel("media.deleted", "fa")).toBe("فایل رسانه حذف شد");
    expect(auditEventLabel("media.not_a_real_event", "en")).toBeNull();
    expect(auditEventLabel("media.not_a_real_event", "fa")).toBeNull();
    expect(Object.keys(AUDIT_EVENT_LABELS)).not.toContain("media.not_a_real_event");
  });
});
