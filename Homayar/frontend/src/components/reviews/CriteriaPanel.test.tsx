import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import api from "@/lib/api";
import { apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { ReviewCriteriaResponse, ReviewCriterionEntry } from "@/types/reviews";
import CriteriaPanel from "@/components/reviews/CriteriaPanel";

const T = "2026-11-10T12:00:00Z";
const LOAD = { timeout: 5000 };

function machine(outcome: string, overrides: Partial<ReviewCriterionEntry["machine"]> = {}) {
  return {
    outcome,
    reason: null,
    decided_by: null,
    review_required: outcome === "failed" || outcome === "insufficient_evidence",
    review_reasons: [],
    evidence: [{ turn_index: 1 }, { turn_index: 4 }],
    ...overrides,
  };
}

function criterion(overrides: Partial<ReviewCriterionEntry> = {}): ReviewCriterionEntry {
  return {
    criterion_id: "greeting",
    position: 1,
    weight: 30,
    auto_fail: false,
    na_allowed: false,
    reviewable: true,
    machine: machine("passed"),
    decision: null,
    ...overrides,
  };
}

function criteria(overrides: Partial<ReviewCriteriaResponse> = {}): ReviewCriteriaResponse {
  return {
    review_item_id: "rev-1",
    revision: 4,
    status: "in_review",
    scorecard_result_id: "result-1",
    machine: {
      outcome: "failed",
      reason: null,
      complete: true,
      score: 70,
      score_min: 0,
      score_max: 100,
      pass_threshold: 80,
      applicable_weight: 100,
      earned_weight: 70,
      failed_weight: 30,
      insufficient_weight: 0,
    },
    human: {
      outcome: "passed",
      reason: null,
      complete: true,
      score: 90,
      score_min: 0,
      score_max: 100,
      pass_threshold: 80,
      applicable_weight: 100,
      earned_weight: 90,
      failed_weight: 10,
      insufficient_weight: 0,
      final: false,
    },
    criteria: [criterion()],
    ...overrides,
  };
}

function mockCriteria(data: ReviewCriteriaResponse) {
  return vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url === "/review-items/rev-1/criteria") return okResponse(data);
    throw new Error(`unexpected read ${url}`);
  });
}

function renderPanel(data: ReviewCriteriaResponse, { isAssignee = true, canManage = true, status = "in_review" } = {}) {
  window.localStorage.removeItem("homayar_lang");
  void data;
  return renderConsole(
    <CriteriaPanel itemId="rev-1" itemStatus={status} isAssignee={isAssignee} canManage={canManage} />,
    { lang: "en" },
  );
}

/** The hidden native selects of an open override form, in render order. */
function outcomeSelect(): HTMLSelectElement {
  const select = screen.getAllByTestId("hidden-select-container")[0].querySelector("select");
  if (!select) throw new Error("no outcome select");
  return select;
}

function reasonSelect(): HTMLSelectElement {
  const select = screen.getAllByTestId("hidden-select-container")[1].querySelector("select");
  if (!select) throw new Error("no reason select");
  return select;
}

describe("CriteriaPanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the machine and the human score as two separate blocks, preview before final", async () => {
    mockCriteria(criteria({ criteria: [criterion({ machine: machine("insufficient_evidence") })] }));
    renderPanel(criteria({ criteria: [criterion({ machine: machine("insufficient_evidence") })] }));

    expect(await screen.findByText("Machine score", {}, LOAD)).toBeInTheDocument();
    expect(screen.getByText("Human score")).toBeInTheDocument();
    // Each block shows its own outcome; the two are never one number.
    expect(screen.getByText("Failed", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("Passed", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("90", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("Preview")).toBeInTheDocument();
    expect(screen.queryByText("Final")).not.toBeInTheDocument();
  });

  it("marks the human score Final after completion", async () => {
    mockCriteria(
      criteria({
        status: "completed",
        human: { ...criteria().human!, final: true },
      }),
    );
    renderPanel(criteria({ status: "completed", human: { ...criteria().human!, final: true } }), { status: "completed" });

    expect(await screen.findByText("Final", {}, LOAD)).toBeInTheDocument();
    expect(screen.queryByText("Preview")).not.toBeInTheDocument();
  });

  it("says there is no machine score and shows no criteria or decision controls", async () => {
    mockCriteria(
      criteria({ scorecard_result_id: null, machine: null, human: null, criteria: [] }),
    );
    renderPanel(criteria({ scorecard_result_id: null, machine: null, human: null, criteria: [] }));

    expect(await screen.findByText("This call has no machine score to review.", undefined, LOAD)).toBeInTheDocument();
    expect(screen.queryByText("Machine score")).not.toBeInTheDocument();
    expect(screen.queryByText("Human score")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Override" })).not.toBeInTheDocument();
  });

  it("lists each criterion with weight, auto-fail, machine outcome, reason, evidence and decision", async () => {
    mockCriteria(
      criteria({
        criteria: [
          criterion({ criterion_id: "greeting", machine: machine("passed") }),
          criterion({
            criterion_id: "forbidden_phrase",
            position: 2,
            weight: 70,
            auto_fail: true,
            machine: machine("failed", { reason: "said the forbidden words" }),
            decision: {
              decision: "override",
              reviewer_outcome: "passed",
              reason_code: "evidence_misread",
              note: "The phrase is quoted, not used.",
              evidence: [2],
              decided_by_user_id: "user-1",
              updated_at: T,
            },
          }),
        ],
      }),
    );
    renderPanel(criteria());

    const greetingRow = (await screen.findByText("greeting", {}, LOAD)).closest("li") as HTMLElement;
    const forbiddenRow = screen.getByText("forbidden_phrase").closest("li") as HTMLElement;
    expect(screen.getByText("Auto-fail")).toBeInTheDocument();
    expect(screen.getByText(/said the forbidden words/)).toBeInTheDocument();
    expect(within(forbiddenRow).getByText(/2 turn/)).toBeInTheDocument();
    // The current decision: kind (chip next to the criterion's own Override
    // button), reviewer outcome, reason label, note.
    expect(screen.getAllByText("Override").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/Evidence misread/)).toBeInTheDocument();
    expect(screen.getByText(/The phrase is quoted, not used\./)).toBeInTheDocument();
    void greetingRow;
  });

  it("disables Accept for insufficient evidence and explains; Accept works for passed", async () => {
    mockCriteria(
      criteria({
        criteria: [
          criterion({ criterion_id: "weak", machine: machine("insufficient_evidence") }),
          criterion({ criterion_id: "solid", position: 2, machine: machine("passed") }),
        ],
      }),
    );
    renderPanel(criteria());

    const weak = (await screen.findByText("weak", {}, LOAD)).closest("li")!;
    const solid = screen.getByText("solid").closest("li")!;
    expect(within(weak as HTMLElement).getByRole("button", { name: "Accept" })).toBeDisabled();
    expect(within(weak as HTMLElement).getByText("Insufficient evidence can only be overridden.")).toBeInTheDocument();
    expect(within(solid as HTMLElement).getByRole("button", { name: "Accept" })).toBeEnabled();
  });

  it("offers not_applicable only when na_allowed, and never the machine outcome itself", async () => {
    mockCriteria(
      criteria({
        criteria: [
          criterion({ criterion_id: "strict", na_allowed: false, machine: machine("failed") }),
          criterion({ criterion_id: "flexible", position: 2, na_allowed: true, machine: machine("failed") }),
        ],
      }),
    );
    renderPanel(criteria());

    const user = userEvent.setup();
    const strict = (await screen.findByText("strict", {}, LOAD)).closest("li") as HTMLElement;
    await user.click(within(strict).getByRole("button", { name: "Override" }));
    // The machine outcome (failed) is not offered; na is not allowed here.
    // (The leading "" is the hidden select's own placeholder.)
    expect(Array.from(outcomeSelect().options).map((option) => option.value)).toEqual(["", "unset", "passed"]);

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    const flexible = screen.getByText("flexible").closest("li") as HTMLElement;
    await user.click(within(flexible).getByRole("button", { name: "Override" }));
    expect(Array.from(outcomeSelect().options).map((option) => option.value)).toEqual([
      "",
      "unset",
      "passed",
      "not_applicable",
    ]);
  });

  it("shows no decision control for a non-reviewable criterion", async () => {
    mockCriteria(criteria({ criteria: [criterion({ criterion_id: "whole_call", reviewable: false })] }));
    renderPanel(criteria());

    const row = (await screen.findByText("whole_call", {}, LOAD)).closest("li")!;
    expect(within(row as HTMLElement).getByText("Not reviewable for this call.")).toBeInTheDocument();
    expect(within(row as HTMLElement).queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
    expect(within(row as HTMLElement).queryByRole("button", { name: "Override" })).not.toBeInTheDocument();
  });

  it("hides every decision control from someone who is not the assignee", async () => {
    mockCriteria(criteria());
    renderPanel(criteria(), { isAssignee: false });

    expect(await screen.findByText("greeting", {}, LOAD)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Override" })).not.toBeInTheDocument();
  });

  it("hides every decision control without review:manage", async () => {
    mockCriteria(criteria());
    renderPanel(criteria(), { canManage: false });

    expect(await screen.findByText("greeting", {}, LOAD)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
  });

  it("blocks a blank note with a message and no request", async () => {
    const post = vi.spyOn(api, "post").mockImplementation(async () => {
      throw new Error("no write may happen");
    });
    mockCriteria(criteria());
    renderPanel(criteria());

    const user = userEvent.setup();
    const row = (await screen.findByText("greeting", {}, LOAD)).closest("li") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Override" }));
    fireEvent.change(outcomeSelect(), { target: { value: "failed" } });
    fireEvent.change(reasonSelect(), { target: { value: "other" } });
    await user.type(screen.getByLabelText("Evidence turns"), "3");
    fireEvent.click(screen.getByRole("button", { name: "Save decision" }));

    expect(await screen.findByText("Write a note explaining the override.", undefined, LOAD)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("blocks a missing outcome, a missing reason and bad evidence, each with its own text", async () => {
    const post = vi.spyOn(api, "post").mockImplementation(async () => {
      throw new Error("no write may happen");
    });
    mockCriteria(criteria());
    renderPanel(criteria());

    const user = userEvent.setup();
    const row = (await screen.findByText("greeting", {}, LOAD)).closest("li") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Override" }));

    // Nothing chosen at all.
    fireEvent.click(screen.getByRole("button", { name: "Save decision" }));
    expect(await screen.findByText("Choose the reviewer outcome.", undefined, LOAD)).toBeInTheDocument();

    fireEvent.change(outcomeSelect(), { target: { value: "failed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save decision" }));
    expect(await screen.findByText("Choose a reason code.", undefined, LOAD)).toBeInTheDocument();

    fireEvent.change(reasonSelect(), { target: { value: "other" } });
    await user.type(screen.getByLabelText("Note"), "a real note");
    fireEvent.click(screen.getByRole("button", { name: "Save decision" }));
    expect(await screen.findByText("List at least one evidence turn.", undefined, LOAD)).toBeInTheDocument();

    await user.type(screen.getByLabelText("Evidence turns"), "3,x");
    fireEvent.click(screen.getByRole("button", { name: "Save decision" }));
    expect(await screen.findByText("Evidence turns must be whole numbers of 0 or more.", undefined, LOAD)).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Evidence turns"));
    await user.type(screen.getByLabelText("Evidence turns"), "3,3");
    fireEvent.click(screen.getByRole("button", { name: "Save decision" }));
    expect(await screen.findByText("Evidence turns must be distinct.", undefined, LOAD)).toBeInTheDocument();

    expect(post).not.toHaveBeenCalled();
  }, 10_000);

  it("sends the exact override body, with the turns sorted ascending", async () => {
    const post = vi
      .spyOn(api, "post")
      .mockResolvedValue(
        okResponse(
          criteria({
            revision: 5,
            criteria: [
              criterion({
                machine: machine("failed"),
                decision: {
                  decision: "override",
                  reviewer_outcome: "passed",
                  reason_code: "evidence_misread",
                  note: "Read again.",
                  evidence: [7, 3],
                  decided_by_user_id: "user-1",
                  updated_at: T,
                },
              }),
            ],
          }),
        ),
      );
    mockCriteria(criteria({ criteria: [criterion({ machine: machine("failed") })] }));
    renderPanel(criteria({ criteria: [criterion({ machine: machine("failed") })] }));

    const user = userEvent.setup();
    const row = (await screen.findByText("greeting", {}, LOAD)).closest("li") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Override" }));
    fireEvent.change(outcomeSelect(), { target: { value: "passed" } });
    fireEvent.change(reasonSelect(), { target: { value: "evidence_misread" } });
    await user.type(screen.getByLabelText("Note"), "Read again.");
    await user.type(screen.getByLabelText("Evidence turns"), "7, 3");
    fireEvent.click(screen.getByRole("button", { name: "Save decision" }));

    await waitFor(
      () =>
        expect(post).toHaveBeenCalledWith("/review-items/rev-1/criteria/greeting/decision", {
          expected_revision: 4,
          decision: "override",
          reviewer_outcome: "passed",
          reason_code: "evidence_misread",
          note: "Read again.",
          evidence_turn_indexes: [3, 7],
        }),
      LOAD,
    );
  }, 10_000);

  it.each(["۳، ۷", "۳,۷", "3، 7"])("sends Persian evidence turns %s as [3, 7]", async (typed) => {
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse(criteria({ revision: 5 })));
    mockCriteria(criteria({ criteria: [criterion({ machine: machine("failed") })] }));
    renderPanel(criteria({ criteria: [criterion({ machine: machine("failed") })] }));

    const user = userEvent.setup();
    const row = (await screen.findByText("greeting", {}, LOAD)).closest("li") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Override" }));
    fireEvent.change(outcomeSelect(), { target: { value: "passed" } });
    fireEvent.change(reasonSelect(), { target: { value: "other" } });
    await user.type(screen.getByLabelText("Note"), "Seen in the call.");
    fireEvent.change(screen.getByLabelText("Evidence turns"), { target: { value: typed } });
    fireEvent.click(screen.getByRole("button", { name: "Save decision" }));

    await waitFor(
      () =>
        expect(post).toHaveBeenCalledWith(
          "/review-items/rev-1/criteria/greeting/decision",
          expect.objectContaining({ evidence_turn_indexes: [3, 7] }),
        ),
      LOAD,
    );
  }, 10_000);

  it("sends an accept with only the revision and the decision", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse(criteria({ revision: 5 })));
    mockCriteria(criteria());
    renderPanel(criteria());

    fireEvent.click(await screen.findByRole("button", { name: "Accept" }, LOAD));
    await waitFor(
      () =>
        expect(post).toHaveBeenCalledWith("/review-items/rev-1/criteria/greeting/decision", {
          expected_revision: 4,
          decision: "accept",
        }),
      LOAD,
    );
  });

  it("shows the mapped text of a refused decision", async () => {
    vi.spyOn(api, "post").mockRejectedValue(apiError(422, "override_matches_machine"));
    mockCriteria(criteria());
    renderPanel(criteria());

    fireEvent.click(await screen.findByRole("button", { name: "Accept" }, LOAD));
    expect(
      await screen.findByText("The chosen outcome matches the machine; use Accept to confirm it.", undefined, LOAD),
    ).toBeInTheDocument();
  });
});
