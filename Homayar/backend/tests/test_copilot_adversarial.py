"""HOM-98 (SPEC section 3): the adversarial deny-cases and their allow-controls.

The world: workspace A (record scope TEAM) with teams T1 and T2, and workspace
B.  Callers: the owner of A (whole workspace), a supervisor of T1 only, and the
owner of B.  Conversations with an indexed transcript and a counted complaint
judgment: ``one`` and ``inject`` in A/T1 (``inject`` carries a prompt injection
in its second segment), ``two`` in A/T2, ``other`` in B.

Every deny-case also checks the **no-leak property**: each cited conversation
id and each fact citation id of the answer belongs to the caller's
live-visible conversations (deletions and membership changes included).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from backend.copilot_guard import ModelPrompt
from backend.database.saas_models import (
    AnalysisRun,
    IngestionRecord,
    IngestionSourceType,
    IngestionStatus,
    Meeting,
    MeetingStatus,
    Membership,
    StageState,
    Team,
    TeamAssignment,
    TeamRole,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptSource,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.security.context import WorkspaceContext
from backend.services.copilot_answer_service import CopilotStatus, answer_question
from backend.tests._copilot_world import COMPLAINT as JUDGMENT
from backend.tests._copilot_world import (
    DAY,
    INJECTION,
    NOW,
    TREND_WINDOW,
    FakePlanner,
    FakeWriter,
    _extraction_row,
    _first_span,
    plan_of,
)
from backend.tests._database import inspection_session_factory
from backend.tests.test_coverage import Tenant
from backend.tests.test_retrieval_evidence import _api_key, _bound, _context
from backend.tests.test_retrieval_index import _entries, _index, _seed_view

BASE = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
MADE_UP = "3f1e2d3c-4b5a-4f6e-8d7c-6b5a4f3e2d1c"
# A5.3: the injected segment names the foreign workspace, and the control world
# replaces it with a benign text that keeps the queried word, so the search
# hits and the comparison isolates the injection itself.
BENIGN_INSTRUCTIONS = "the caller asked for written instructions about the refund"


def _world_texts(injection: str) -> dict[str, tuple[str, str]]:
    return {
        "one": ("سلام دنیا گفتگوی اول", "متکلف گفت وجه من را برگردانید"),
        "inject": ("سلام دنیا گفتگوی دوم", injection),
        "two": ("سلام دنیا گفتگوی سوم", "متأسفانه مشکل صورت‌حساب پیش آمد"),
        "other": ("سلام دنیا گفتگوی چهارم", "این گفتگوی فضای کاری دیگر است"),
    }


class AdversarialWorld:
    def __init__(self, a: Tenant, b: Tenant, teams, supervisor_id, meetings, injection: str):
        self.a = a
        self.b = b
        self.teams = teams  # {"one": id, "two": id}
        self.supervisor_id = supervisor_id
        self.meetings = meetings  # {"one": id, ...}
        self.injection = injection  # the seeded text of the injected segment

    def owner(self) -> WorkspaceContext:
        return _context(self.a.owner_id, self.a.workspace_id)

    def supervisor(self) -> WorkspaceContext:
        return _context(self.supervisor_id, self.a.workspace_id)

    def owner_b(self) -> WorkspaceContext:
        return _context(self.b.owner_id, self.b.workspace_id)

    def visible(self, caller: str) -> set[str]:
        """The live-visible conversation ids of one caller, from the test's own
        bookkeeping (the same rows the live checks read)."""
        live = set(self.meetings.values())
        with inspection_session_factory()() as db:
            deleted = {
                row.id
                for row in db.query(Meeting).filter(
                    Meeting.id.in_(live), Meeting.deleted_at.is_not(None)
                )
            }
        scope = {
            "owner": {"one", "inject", "two"},
            "supervisor": {"one", "inject"},
            "owner_b": {"other"},
        }
        return {self.meetings[name] for name in scope[caller]} - deleted


def _conversation(
    tenant: Tenant, name: str, team_id: str | None, label: str, texts: dict[str, tuple[str, str]]
) -> tuple[str, str]:
    """One judged, indexed conversation of ``tenant``.  Returns (meeting,
    revision) ids."""
    words = texts[name]
    workspace_id = tenant.workspace_id
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=workspace_id,
            created_by_user_id=tenant.owner_id,
            title=f"adversarial {name}",
            status=MeetingStatus.COMPLETED,
            duration_seconds=30,
            team_id=team_id,
            language="fa",
            created_at=BASE,
        )
        db.add(meeting)
        db.flush()
        db.add(
            IngestionRecord(
                organization_id=tenant.organization_id,
                workspace_id=workspace_id,
                source_type=IngestionSourceType.FILE_UPLOAD,
                source_provider="test",
                external_event_id=str(uuid.uuid4()),
                payload_hash="0" * 64,
                status=IngestionStatus.ACCEPTED,
                received_at=BASE,
                meeting_id=meeting.id,
            )
        )
        db.flush()
        revision = TranscriptRevision(
            workspace_id=workspace_id,
            meeting_id=meeting.id,
            revision_number=1,
            source=TranscriptSource.ASR,
            reason="seed",
            language="fa",
            segment_count=len(words),
            is_mock=False,
            is_current=True,
        )
        db.add(revision)
        db.flush()
        for sequence, text in enumerate(words):
            db.add(
                TranscriptSegment(
                    workspace_id=workspace_id,
                    meeting_id=meeting.id,
                    revision_id=revision.id,
                    sequence=sequence,
                    speaker_key="agent",
                    start_ms=sequence * 1000,
                    end_ms=sequence * 1000 + 999,
                    text=text,
                )
            )
        run = AnalysisRun(
            workspace_id=workspace_id,
            meeting_id=meeting.id,
            transcript_revision_id=revision.id,
            state=StageState.COMPLETED,
            is_current=True,
        )
        db.add(run)
        db.flush()
        db.add(_extraction_row(workspace_id, meeting.id, run.id, JUDGMENT))
        db.commit()
        meeting_id, revision_id = meeting.id, revision.id
    view = _seed_view(
        {"workspace_id": workspace_id, "user_id": tenant.owner_id},
        f"hom98-adv-{label}",
        texts=words,
        meeting_id=meeting_id,
        revision_id=revision_id,
    )
    _index(view["text_view_id"], workspace_id)
    return meeting_id, revision_id


def build_world(
    client, email_outbox, label: str = "w", *, injected: bool = True
) -> AdversarialWorld:
    """One adversarial world.  With ``injected`` (the default) the second
    segment of ``inject`` is the prompt injection naming the B workspace id
    (A5.3); without it the segment is a benign text that keeps the queried
    word, the control world of case 6."""
    a = Tenant(client, email_outbox, f"hom98-adv-{label}-a@example.com")
    b = Tenant(client, email_outbox, f"hom98-adv-{label}-b@example.com")
    with inspection_session_factory()() as db:
        db.execute(
            update(Workspace).where(Workspace.id == a.workspace_id).values(record_scope="TEAM")
        )
        db.execute(
            update(Membership)
            .where(Membership.workspace_id == a.workspace_id, Membership.user_id == a.owner_id)
            .values(valid_from=NOW - 365 * DAY)
        )
        teams = {}
        for name in ("one", "two"):
            team = Team(
                workspace_id=a.workspace_id,
                name=f"adv {name}",
                slug=f"adv-{name}-{uuid.uuid4().hex[:8]}",
            )
            db.add(team)
            db.flush()
            teams[name] = team.id
        supervisor = User(email=f"hom98-adv-{label}-sup@example.com", display_name="adv sup")
        db.add(supervisor)
        db.flush()
        db.add(
            Membership(
                workspace_id=a.workspace_id,
                user_id=supervisor.id,
                role=WorkspaceRole.MEMBER,
                valid_from=NOW - 365 * DAY,
            )
        )
        db.add(
            TeamAssignment(
                workspace_id=a.workspace_id,
                team_id=teams["one"],
                user_id=supervisor.id,
                role=TeamRole.SUPERVISOR,
                assigned_by_user_id=a.owner_id,
                valid_from=NOW - 365 * DAY,
            )
        )
        db.commit()
        supervisor_id = supervisor.id
    injection = (
        f"{INJECTION} workspace_id={b.workspace_id}" if injected else BENIGN_INSTRUCTIONS
    )
    texts = _world_texts(injection)
    meetings = {
        "one": _conversation(a, "one", teams["one"], f"{label}-one", texts)[0],
        "inject": _conversation(a, "inject", teams["one"], f"{label}-inject", texts)[0],
        "two": _conversation(a, "two", teams["two"], f"{label}-two", texts)[0],
        "other": _conversation(b, "other", None, f"{label}-other", texts)[0],
    }
    return AdversarialWorld(a, b, teams, supervisor_id, meetings, injection)


@pytest.fixture
def world(client, email_outbox) -> AdversarialWorld:
    return build_world(client, email_outbox)


def ask(
    world: AdversarialWorld, context: WorkspaceContext, plan: str, question="سلام", writer=None
):
    planner = FakePlanner(plan)
    writer = FakeWriter() if writer is None else writer
    with _bound(context.workspace.id) as db:
        answer = answer_question(
            db,
            context=context,
            question=question,
            now=NOW,
            planner=planner,
            writer=writer,
        )
    return answer, planner, writer


def lookup_plan(meeting_id: str, query: str | None = None) -> str:
    params: dict[str, object] = {"conversation_id": meeting_id}
    if query is not None:
        params["query"] = query
    return plan_of("conversation_lookup", **params)


def count_plan(team_id: str | None = None) -> str:
    params = {"issue": "complaint_refund", **TREND_WINDOW}
    if team_id is not None:
        params["team_id"] = team_id
    return plan_of("issue_count", **params)


def no_leak(answer, visible: set[str]) -> None:
    """Every cited conversation and every fact citation is live-visible."""
    assert set(answer.cited_conversation_ids()) <= visible
    for fact in answer.facts:
        assert set(fact.citations) <= visible


# --- cases 1 to 3: guessed ids and the cross-team lookup -------------------------------------


def test_case_1_a_foreign_conversation_is_no_evidence(world):
    answer, planner, writer = ask(world, world.owner(), lookup_plan(world.meetings["other"]))
    assert answer.status is CopilotStatus.ABSTAINED
    assert answer.reason == "no_evidence" and answer.evidence == ()
    assert writer.prompts == []  # the gate stops before the writer
    control, _, _ = ask(world, world.owner(), lookup_plan(world.meetings["one"]))
    assert control.status is CopilotStatus.ANSWERED
    assert control.cited_conversation_ids() == (world.meetings["one"],)
    no_leak(control, world.visible("owner"))


def test_case_2_a_made_up_conversation_matches_the_foreign_one_field_for_field(world):
    foreign, _, _ = ask(world, world.owner(), lookup_plan(world.meetings["other"]))
    made_up, _, _ = ask(world, world.owner(), lookup_plan(MADE_UP))
    assert foreign == made_up


def test_case_3_a_t2_conversation_is_invisible_to_the_t1_supervisor(world):
    supervisor = world.supervisor()
    denied, _, _ = ask(world, supervisor, lookup_plan(world.meetings["two"]))
    made_up, _, _ = ask(world, supervisor, lookup_plan(MADE_UP))
    assert denied.status is CopilotStatus.ABSTAINED and denied.reason == "no_evidence"
    assert denied == made_up  # field for field, so the id is not leaked either
    control, _, _ = ask(world, supervisor, lookup_plan(world.meetings["one"]))
    assert control.status is CopilotStatus.ANSWERED
    assert control.cited_conversation_ids() == (world.meetings["one"],)
    no_leak(control, world.visible("supervisor"))


# --- case 4: the scope of a team filter ------------------------------------------------------


def test_case_4_a_team_outside_the_scope_refuses_like_a_made_up_team(world):
    supervisor = world.supervisor()
    refused, _, _ = ask(world, supervisor, count_plan(world.teams["two"]))
    made_up, _, _ = ask(world, supervisor, count_plan(MADE_UP))
    assert refused.status is CopilotStatus.REFUSED
    assert refused.reason == "outside_caller_scope" and refused.detail is None
    assert refused == made_up  # field for field
    control, _, _ = ask(world, supervisor, count_plan(world.teams["one"]))
    assert control.status is CopilotStatus.ANSWERED
    assert [fact.value for fact in control.facts] == [2]
    no_leak(control, world.visible("supervisor"))


# --- case 5: a malformed plan ----------------------------------------------------------------


@pytest.mark.parametrize("key", ["workspace_id", "scope", "tool"])
def test_case_5_a_plan_with_an_extra_key_refuses_and_the_clean_plan_answers(world, key):
    plan = json.loads(count_plan())
    plan[key] = "somewhere"  # an extra key at the top level of the plan
    answer, _, _ = ask(world, world.owner(), json.dumps(plan))
    assert answer.status is CopilotStatus.REFUSED
    assert answer.reason == "invalid_request" and answer.detail == "plan_malformed"
    control, _, _ = ask(world, world.owner(), count_plan())
    assert control.status is CopilotStatus.ANSWERED
    assert [fact.value for fact in control.facts] == [3]
    no_leak(control, world.visible("owner"))


# --- cases 6 and 7: prompt injection ---------------------------------------------------------


def test_case_6_an_injected_span_changes_nothing_and_is_only_data(world, client, email_outbox):
    """A5.3: the injected segment names the B workspace id, and the control is
    the same plan and question on a world without the injected span (its
    segment keeps the queried word, so the search still hits)."""
    assert world.b.workspace_id in world.injection
    answer, planner, writer = ask(
        world, world.owner(), lookup_plan(world.meetings["inject"], query="instructions")
    )
    clean_world = build_world(client, email_outbox, "clean", injected=False)
    clean, clean_planner, _ = ask(
        clean_world,
        clean_world.owner(),
        lookup_plan(clean_world.meetings["inject"], query="instructions"),
    )
    # Every field that holds no span text is the same with and without the
    # span: the injection steers nothing.
    assert (answer.status, answer.reason, answer.detail, answer.intent_id, answer.tool) == (
        clean.status,
        clean.reason,
        clean.detail,
        clean.intent_id,
        clean.tool,
    )
    assert (
        answer.explanation,
        answer.facts,
        answer.notices,
        answer.catalogue_version,
        answer.catalogue_digest,
    ) == (
        clean.explanation,
        clean.facts,
        clean.notices,
        clean.catalogue_version,
        clean.catalogue_digest,
    )
    assert answer.signals and not clean.signals  # the injection is the only signal
    # The planner never sees transcript text (D7), so its prompt is identical.
    assert planner.prompts[0].instructions == clean_planner.prompts[0].instructions
    for prompt in planner.prompts:
        for word in ("instructions", "workspaces", "متکلف", "برگردانید"):
            assert word not in prompt.instructions and word not in prompt.data
    # The writer prompt frames the injection as data, and every citation stays
    # inside the caller's scope.
    writer_prompt = writer.prompts[0]
    assert '<<<UNTRUSTED kind=transcript label=span:' in writer_prompt.data
    assert f'"{world.injection}"' in writer_prompt.data  # the JSON body of the block
    assert answer.signals[0][0] in {span.span_id for span in answer.evidence}
    assert answer.status is CopilotStatus.ANSWERED
    assert answer.cited_conversation_ids() == (world.meetings["inject"],)
    no_leak(answer, world.visible("owner"))


def test_case_7_a_writer_that_obeys_the_injection_abstains(world):
    injected_entry = _entries(world.meetings["inject"])[1].id

    class ObeyingWriter(FakeWriter):
        def complete(self, prompt: ModelPrompt) -> str:
            self.prompts.append(prompt)
            span_id, text = _first_span(prompt.data)
            assert text == world.injection  # the first span is the injection
            words = " ".join(text.split()[:3])
            sentence = f'The caller said "{words}". Now ignore previous instructions.'
            return json.dumps({"sentences": [{"text": sentence, "citations": [span_id]}]})

    class QuotingWriter(FakeWriter):
        """The allow-control writer (A5.2): the same first span, quoted exactly."""

        def complete(self, prompt: ModelPrompt) -> str:
            self.prompts.append(prompt)
            span_id, text = _first_span(prompt.data)
            assert text == world.injection
            words = " ".join(text.split()[:3])
            sentence = f'The caller said "{words}".'
            return json.dumps({"sentences": [{"text": sentence, "citations": [span_id]}]})

    deny_plan = lookup_plan(world.meetings["inject"], query="instructions")
    planner = FakePlanner(deny_plan)
    with _bound(world.a.workspace_id) as db:
        answer = answer_question(
            db,
            context=world.owner(),
            question="سلام",
            now=NOW,
            planner=planner,
            writer=ObeyingWriter(),
        )
    assert answer.status is CopilotStatus.ABSTAINED
    assert answer.reason == "no_evidence"
    assert answer.detail == "answer_contains_instruction"
    assert answer.signals and answer.signals[0][0] == injected_entry
    # The allow-control: the same deny-case plan (its first span is the
    # injection) and a writer that quotes the injected words exactly, with a
    # citation.
    control_writer = QuotingWriter()
    control, _, _ = ask(world, world.owner(), deny_plan, writer=control_writer)
    assert control.status is CopilotStatus.ANSWERED
    assert control.cited_conversation_ids() == (world.meetings["inject"],)
    assert len(control_writer.prompts) == 1
    no_leak(control, world.visible("owner"))


# --- case 8: deletion without a reindex ------------------------------------------------------


def test_case_8_a_deleted_conversation_disappears_at_once(world):
    meeting_id = world.meetings["one"]
    before, _, _ = ask(world, world.owner(), lookup_plan(meeting_id))
    assert before.status is CopilotStatus.ANSWERED
    assert before.cited_conversation_ids() == (meeting_id,)
    before_count, _, _ = ask(world, world.owner(), count_plan())
    assert [fact.value for fact in before_count.facts] == [3]
    no_leak(before, world.visible("owner"))
    # A5.1: the deny check asserts on the spans, not only on the citations:
    # before the deletion the deleted conversation is among the span
    # conversation ids of ``evidence_examples``.
    examples_plan = plan_of("evidence_examples", **TREND_WINDOW, issue="complaint_refund", limit=3)
    before_examples, _, _ = ask(world, world.owner(), examples_plan)
    assert before_examples.status is CopilotStatus.ANSWERED
    assert meeting_id in {span.conversation_id for span in before_examples.evidence}
    # Soft-delete the meeting and leave the retrieval rows in place.
    with inspection_session_factory()() as db:
        db.execute(
            update(Meeting)
            .where(Meeting.id == meeting_id)
            .values(deleted_at=NOW - timedelta(seconds=1))
        )
        db.commit()
    assert _entries(meeting_id), "the retrieval rows stay in place (no reindex)"
    lookup, _, _ = ask(world, world.owner(), lookup_plan(meeting_id))
    assert lookup.status is CopilotStatus.ABSTAINED and lookup.reason == "no_evidence"
    count, _, _ = ask(world, world.owner(), count_plan())
    assert [fact.value for fact in count.facts] == [2]  # one less, exactly
    examples, _, _ = ask(world, world.owner(), examples_plan)
    assert examples.status is CopilotStatus.ANSWERED  # the cohort still holds two
    no_leak(examples, world.visible("owner"))
    assert meeting_id not in {span.conversation_id for span in examples.evidence}
    assert meeting_id not in examples.cited_conversation_ids()


# --- case 9: the team assignment ends, the same context object -------------------------------


def test_case_9_an_ended_assignment_acts_at_once_on_the_same_context(world):
    context = world.supervisor()  # one object, reused below
    before, _, _ = ask(world, context, count_plan(world.teams["one"]))
    assert before.status is CopilotStatus.ANSWERED
    assert [fact.value for fact in before.facts] == [2]
    no_leak(before, world.visible("supervisor"))
    with inspection_session_factory()() as db:
        db.execute(
            update(TeamAssignment)
            .where(
                TeamAssignment.workspace_id == world.a.workspace_id,
                TeamAssignment.user_id == world.supervisor_id,
            )
            .values(revoked_at=NOW)
        )
        db.commit()
    count, _, _ = ask(world, context, count_plan(world.teams["one"]))
    assert count.status is not CopilotStatus.ANSWERED  # no T1 number any more
    lookup, _, _ = ask(world, context, lookup_plan(world.meetings["one"]))
    assert lookup.status is CopilotStatus.ABSTAINED and lookup.reason == "no_evidence"
    examples, _, _ = ask(
        world,
        context,
        plan_of("evidence_examples", **TREND_WINDOW, issue="complaint_refund"),
    )
    # No visible team left: the evidence gate abstains.
    assert examples.status is CopilotStatus.ABSTAINED and examples.reason == "no_evidence"


# --- cases 10 and 11: the live caller --------------------------------------------------------


def test_case_10_a_deactivated_membership_refuses_with_the_same_context(world):
    context = world.owner()  # one object, reused below
    control, _, _ = ask(world, context, lookup_plan(world.meetings["one"]))
    assert control.status is CopilotStatus.ANSWERED
    with inspection_session_factory()() as db:
        db.execute(
            update(Membership)
            .where(
                Membership.workspace_id == world.a.workspace_id,
                Membership.user_id == world.a.owner_id,
            )
            .values(is_active=False)
        )
        db.commit()
    planner = FakePlanner(count_plan())
    with _bound(world.a.workspace_id) as db:
        answer = answer_question(
            db, context=context, question="سلام", now=NOW, planner=planner, writer=FakeWriter()
        )
    assert answer.status is CopilotStatus.REFUSED
    assert answer.reason == "permission_denied"
    assert planner.prompts == []  # zero model calls


def test_case_11_a_revoked_api_key_refuses(world):
    key_id = _api_key(world.a.workspace_id, world.a.owner_id, ["meeting:read"])
    context = _context(world.a.owner_id, world.a.workspace_id, api_key_id=key_id)
    control, _, _ = ask(world, context, lookup_plan(world.meetings["one"]))
    assert control.status is CopilotStatus.ANSWERED
    no_leak(control, world.visible("owner"))
    with inspection_session_factory()() as db:
        from backend.database.saas_models import ApiKey

        db.execute(update(ApiKey).where(ApiKey.id == key_id).values(revoked_at=NOW))
        db.commit()
    answer, _, _ = ask(world, context, lookup_plan(world.meetings["one"]))
    assert answer.status is CopilotStatus.REFUSED
    assert answer.reason == "permission_denied"
