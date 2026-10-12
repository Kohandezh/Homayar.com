import { describe, expect, it } from "vitest";
import {
  auditQueryParams,
  hasAuditFilters,
  NO_AUDIT_FILTERS,
  readAuditFilters,
  writeAuditFilters,
  type AuditFilters,
} from "@/lib/audit-filters";

function filters(overrides: Partial<AuditFilters> = {}): AuditFilters {
  return { ...NO_AUDIT_FILTERS, ...overrides };
}

describe("audit filters in the URL", () => {
  it("reads every filter the API accepts", () => {
    const search = new URLSearchParams(
      "event_type=member.role_changed&outcome=failure&actor=user-1&target_type=membership&target_id=mem-2&from=2026-09-01&to=2026-09-30",
    );
    expect(readAuditFilters(search)).toEqual({
      eventType: "member.role_changed",
      outcome: "failure",
      actorUserId: "user-1",
      targetType: "membership",
      targetId: "mem-2",
      from: "2026-09-01",
      to: "2026-09-30",
    });
  });

  it("drops a day that is not a real date, so a hand-edited link still loads", () => {
    const search = new URLSearchParams("from=yesterday&to=2026-02-30");
    expect(readAuditFilters(search)).toEqual(filters());
    expect(hasAuditFilters(readAuditFilters(search))).toBe(false);
  });

  it("writes only the chosen filters and keeps unrelated query parameters", () => {
    const base = new URLSearchParams("tab=audit&outcome=success");
    const written = writeAuditFilters(filters({ eventType: "meeting.deleted" }), base);
    expect(written.get("event_type")).toBe("meeting.deleted");
    expect(written.get("tab")).toBe("audit");
    // Cleared filters are removed instead of written as blanks.
    expect(written.has("outcome")).toBe(false);
    expect(written.has("from")).toBe(false);
  });

  it("round-trips the filters through the URL", () => {
    const chosen = filters({ outcome: "failure", actorUserId: "user-1", to: "2026-09-30" });
    expect(readAuditFilters(writeAuditFilters(chosen))).toEqual(chosen);
  });
});

describe("audit filters as query parameters", () => {
  it("sends the page size and nothing else when nothing is filtered", () => {
    expect(auditQueryParams(filters(), { timeZone: "UTC" })).toEqual({ limit: 25 });
  });

  it("maps each filter to its API name and adds the cursor", () => {
    const params = auditQueryParams(
      filters({
        eventType: "member.role_changed",
        outcome: "failure",
        actorUserId: "user-1",
        targetType: "membership",
        targetId: "mem-2",
      }),
      { cursor: "cursor-2", timeZone: "UTC", limit: 50 },
    );
    expect(params).toEqual({
      limit: 50,
      cursor: "cursor-2",
      event_type: "member.role_changed",
      outcome: "failure",
      actor_user_id: "user-1",
      target_type: "membership",
      target_id: "mem-2",
    });
  });

  it("includes the whole last day, because occurred_before is exclusive", () => {
    const params = auditQueryParams(filters({ from: "2026-09-01", to: "2026-09-30" }), {
      timeZone: "UTC",
    });
    expect(params.occurred_after).toBe("2026-09-01T00:00:00.000Z");
    expect(params.occurred_before).toBe("2026-10-01T00:00:00.000Z");
  });

  it("reads the chosen days in the reader's own time zone", () => {
    const params = auditQueryParams(filters({ from: "2026-09-01", to: "2026-09-30" }), {
      timeZone: "Asia/Tehran",
    });
    expect(params.occurred_after).toBe("2026-08-31T20:30:00.000Z");
    expect(params.occurred_before).toBe("2026-09-30T20:30:00.000Z");
  });

  it("accepts one end of the range on its own", () => {
    const params = auditQueryParams(filters({ from: "2026-09-01" }), { timeZone: "UTC" });
    expect(params.occurred_after).toBe("2026-09-01T00:00:00.000Z");
    expect(params.occurred_before).toBeUndefined();
  });
});
