"""HOM-354: the subject export covers the HOM-56 scorecard tables.

The stored QA scorecard of a conversation and its criterion rows are personal
data about the call's agent and customer.  The export (ADR 0025, extended by
ADR 0079) lists them for the people they are about:

* the customer path: a matched conversation carries its results (``scorecards``,
  an empty list with none), and a result never makes a conversation match on
  its own;
* the agent path: a user whose account e-mail or phone (``users.email``,
  ``users.phone_e164``) is a requested identifier gets every live conversation
  of the workspace where they were the agent, scorecard rows only, no
  transcript, summary, action or extraction text;
* the same rules as the rest of the export: the workspace filter, live
  conversations, the caller's record access, stored values exactly as stored
  (a redacted evidence phrase stays redacted), and the conversation purge
  (ADR 0021) still removes both tables.

Synthetic data only (``example.org`` e-mails, ``09xx`` phones).
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, true, update

from backend.database.saas_models import (
    AuditEvent,
    CriterionResultRecord,
    Meeting,
    PiiMapping,
    PurgeState,
    ScorecardResultRecord,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptSource,
    User,
)
from backend.privacy.subject_export import build_subject_export, parse_subject_identifier
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_free_text_partial_pii import _redacted_meeting
from backend.tests.test_ingestion_ledger import _headers, _tenant
from backend.tests.test_privacy_routing import SEGMENTS
from backend.tests.test_retention_purge import _purge
from backend.tests.test_scorecard_result_service import (
    NOW,
    _arguments,
    _criterion,
    _record,
    _run,
    _session,
    _stored,
    _version,
)

AGENT_EMAIL = "agent354@example.org"
AGENT_EMAIL_REQUEST = "Agent354@Example.ORG"
AGENT_PHONE_E164 = "+989121234567"  # requested below as 0912 123 4567
CUSTOMER_PHONE = "09198880770"
# The original behind [PHONE_1] of the redacted meeting: kept only as ciphertext
# in a real pii_mappings row, never in the export.
ORIGINAL_PHONE_FORMS = ("09123456789", "۰۹۱۲ ۳۴۵ ۶۷۸۹", "۰۹۱۲۳۴۵۶۷۸۹")
ORIGINAL_EMAIL = "ali.rezaei@example.com"
AGENT_SEGMENTS = ("thanks for calling the support line", "please review my plan")
RESULT_KEYS = {
    "result_id",
    "scorecard_version_id",
    "transcript_revision_id",
    "scorecards_version",
    "checks_version",
    "conversation_type",
    "direction",
    "outcome",
    "reason",
    "complete",
    "pass_threshold",
    "applicable_weight",
    "earned_weight",
    "failed_weight",
    "insufficient_weight",
    "created_at",
    "criteria",
}
CRITERION_KEYS = {
    "position",
    "criterion_id",
    "weight",
    "auto_fail",
    "na_allowed",
    "outcome",
    "reason",
    "decided_by",
    "review_required",
    "review_reasons",
    "check_outcome",
    "check_reason",
    "evidence",
    "candidate_evidence",
}
AGENT_MEETING_KEYS = {"meeting_id", "title", "created_at", "retention_expires_at", "scorecards"}
# Both phrases appear in the check turns; two criteria make one result of two.
TWO_CRITERIA = {
    "name": "Support greeting",
    "pass_threshold": 50,
    "criteria": [_criterion("greeting", 50, "hello"), _criterion("closing", 50, "goodbye")],
}
REDACTED_SPAN = {
    "turn_index": 0,
    "speaker_role": "agent",
    "start_ms": 0,
    "end_ms": 500,
    "phrase": "[PHONE_1]",
    "token_start": 0,
    "token_end": 1,
}
# A2: an evidence phrase that holds the identifier.  An export that matched on
# evidence text would list the silent conversation.
PHONE_IN_EVIDENCE = {
    "turn_index": 0,
    "speaker_role": "customer",
    "start_ms": 0,
    "end_ms": 3000,
    "phrase": f"call me at {CUSTOMER_PHONE}",
    "token_start": 3,
    "token_end": 9,
}


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def settings(settings):
    # One keyed configuration for the API and the processor (mappings retained),
    # as the due-date suite does, so redaction keeps real encrypted originals.
    return settings.model_copy(
        update={"pii_mapping_keys": f"k1:{_key()}", "pii_mapping_active_key_id": "k1"}
    )


def _export(tenant: dict, identifiers: list[str], *, meeting_clause=true(), limit=500):
    with _session(tenant) as db:
        return build_subject_export(
            db,
            workspace_id=tenant["workspace_id"],
            identifiers=[parse_subject_identifier(value) for value in identifiers],
            meeting_clause=meeting_clause,
            now=datetime.now(UTC),
            limit=limit,
        )


def _agent_user(*, email: str = AGENT_EMAIL, phone_e164: str | None = AGENT_PHONE_E164) -> str:
    with inspection_session_factory()() as db:
        user = User(email=email, phone_e164=phone_e164, display_name="Agent")
        db.add(user)
        db.commit()
        return user.id


def _call(
    tenant: dict,
    *,
    texts: tuple[str, ...],
    agent_user_id: str | None = None,
    title: str = "Support call",
    created_at: datetime | None = None,
) -> dict[str, str]:
    """A live meeting of the tenant, its first revision and its segments."""
    columns = {
        "workspace_id": tenant["workspace_id"],
        "created_by_user_id": tenant["user_id"],
        "agent_user_id": agent_user_id,
        "title": title,
        "conversation_type": "call",
        "direction": "inbound",
    }
    if created_at is not None:
        columns["created_at"] = created_at
    with inspection_session_factory()() as db:
        meeting = Meeting(**columns)
        db.add(meeting)
        db.flush()
        revision = TranscriptRevision(
            workspace_id=meeting.workspace_id,
            meeting_id=meeting.id,
            revision_number=1,
            source=TranscriptSource.ASR,
            reason="processing",
            language="en",
            segment_count=len(texts),
            is_current=True,
        )
        db.add(revision)
        db.flush()
        for sequence, text in enumerate(texts):
            db.add(
                TranscriptSegment(
                    workspace_id=meeting.workspace_id,
                    meeting_id=meeting.id,
                    revision_id=revision.id,
                    sequence=sequence,
                    speaker_key="spk_1",
                    start_ms=3000 * sequence,
                    end_ms=3000 * sequence + 3000,
                    text=text,
                )
            )
        db.commit()
        return {"meeting_id": meeting.id, "transcript_revision_id": revision.id}


def _scored(tenant: dict, case: dict[str, str], version_id: str, *, definition=TWO_CRITERIA) -> str:
    result_id, created = _record(tenant, _arguments(tenant, case, version_id, _run(definition)))
    assert created
    return result_id


def _expected_result(result_id: str) -> dict:
    """The D3 shape built from the stored rows: columns only, never the blobs."""
    row, rows = _stored(result_id)
    return {
        "result_id": row.id,
        "scorecard_version_id": row.scorecard_version_id,
        "transcript_revision_id": row.transcript_revision_id,
        "scorecards_version": row.scorecards_version,
        "checks_version": row.checks_version,
        "conversation_type": row.conversation_type,
        "direction": row.direction,
        "outcome": row.outcome,
        "reason": row.reason,
        "complete": row.complete,
        "pass_threshold": row.pass_threshold,
        "applicable_weight": row.applicable_weight,
        "earned_weight": row.earned_weight,
        "failed_weight": row.failed_weight,
        "insufficient_weight": row.insufficient_weight,
        "created_at": row.created_at.isoformat(),
        "criteria": [
            {column: getattr(criterion, column) for column in sorted(CRITERION_KEYS)}
            for criterion in rows
        ],
    }


def _set_evidence(result_id: str, position: int, evidence: list[dict]) -> None:
    """Change one criterion row's stored evidence directly (as the table owner).

    A check run on redacted turns stores placeholder phrases, so this is how a
    stored row can legitimately look."""
    criteria = CriterionResultRecord.__table__
    with inspection_session_factory()() as db:
        db.execute(
            update(criteria)
            .where(criteria.c.result_id == result_id, criteria.c.position == position)
            .values(evidence=evidence)
        )
        db.commit()


def _listed_ids(document: dict) -> set[str]:
    return {entry["meeting_id"] for entry in document["agent"]["meetings"]}


# --- A1, A2: the customer path ----------------------------------------------------------


def test_a1_a_matched_conversation_lists_its_result_and_criteria(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom354-a1")
    case = _call(tenant, texts=(f"my number is {CUSTOMER_PHONE}",))
    version_id = _version(tenant, TWO_CRITERIA)
    result_id = _scored(tenant, case, version_id)

    document, audit = _export(tenant, [CUSTOMER_PHONE])
    (match,) = document["matches"]
    assert match["meeting_id"] == case["meeting_id"]
    assert match["scorecards"] == [_expected_result(result_id)]
    (result,) = match["scorecards"]
    assert set(result) == RESULT_KEYS  # no check_run, result or digest column
    assert set(result["criteria"][0]) == CRITERION_KEYS
    assert [criterion["position"] for criterion in result["criteria"]] == [0, 1]
    assert audit["scorecard_results"] == 1


def test_a2_an_empty_list_without_results_and_results_never_match_alone(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom354-a2")
    matched = _call(tenant, texts=(f"my number is {CUSTOMER_PHONE}",), title="Matched")
    silent = _call(tenant, texts=("nothing personal here",), title="Silent")
    version_id = _version(tenant, TWO_CRITERIA)
    silent_result = _scored(tenant, silent, version_id)  # an unmatched conversation
    # Its evidence phrase holds the identifier: an export that matched on
    # evidence text would list the silent conversation here.
    _set_evidence(silent_result, 0, [PHONE_IN_EVIDENCE])

    document, audit = _export(tenant, [CUSTOMER_PHONE])
    (match,) = document["matches"]
    assert match["meeting_id"] == matched["meeting_id"]
    assert match["scorecards"] == []
    assert audit["scorecard_results"] == 0


# --- A3: the agent path ------------------------------------------------------------------


@pytest.mark.parametrize(
    "identifier",
    [
        pytest.param(AGENT_EMAIL_REQUEST, id="email-any-case"),
        pytest.param("0912 123 4567", id="phone-local-form"),
    ],
)
def test_a3_the_agent_account_identifier_lists_their_scorecards(client, email_outbox, identifier):
    tenant = _tenant(client, email_outbox, "hom354-a3")
    agent_id = _agent_user()
    version_id = _version(tenant, TWO_CRITERIA)
    first = _call(tenant, texts=AGENT_SEGMENTS, agent_user_id=agent_id, title="First")
    second = _call(
        tenant, texts=("a different plain turn",), agent_user_id=agent_id, title="Second"
    )
    _call(tenant, texts=("no result here",), agent_user_id=agent_id, title="No result")
    first_id = _scored(tenant, first, version_id)
    second_id = _scored(tenant, second, version_id)

    document, audit = _export(tenant, [identifier])
    assert document["matches"] == []  # no transcript text holds the identifier
    agent = document["agent"]
    assert agent["truncated"] is False
    listed = {entry["meeting_id"]: entry for entry in agent["meetings"]}
    assert set(listed) == {first["meeting_id"], second["meeting_id"]}
    for entry in listed.values():
        assert set(entry) == AGENT_MEETING_KEYS
    assert listed[first["meeting_id"]]["scorecards"] == [_expected_result(first_id)]
    assert listed[second["meeting_id"]]["scorecards"] == [_expected_result(second_id)]
    assert audit["agent_subjects"] == 1
    assert audit["agent_meetings_scanned"] == 3  # every live agent meeting, result or not
    assert audit["agent_truncated"] is False
    assert audit["scorecard_results"] == 2


def test_a_user_who_is_not_an_agent_of_the_workspace_creates_no_agent_key(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom354-not-agent")
    bystander = _agent_user(email="bystander354@example.org", phone_e164=None)
    case = _call(tenant, texts=AGENT_SEGMENTS)
    _scored(tenant, case, _version(tenant, TWO_CRITERIA))

    document, _audit = _export(tenant, ["bystander354@example.org"])
    assert "agent" not in document

    # Allow-control: the same user made the agent of a live scored conversation.
    with inspection_session_factory()() as db:
        db.get(Meeting, case["meeting_id"]).agent_user_id = bystander
        db.commit()
    document, _audit = _export(tenant, ["bystander354@example.org"])
    assert _listed_ids(document) == {case["meeting_id"]}


# --- A4, A5, A10: scope and bounds -------------------------------------------------------


def test_a4_another_workspaces_conversations_and_results_never_appear(client, email_outbox):
    a = _tenant(client, email_outbox, "hom354-a4-a")
    b = _tenant(client, email_outbox, "hom354-a4-b")
    agent_id = _agent_user()
    a_version = _version(a, TWO_CRITERIA)
    b_version = _version(b, TWO_CRITERIA)
    a_customer = _call(a, texts=(f"my number is {CUSTOMER_PHONE}",), title="A customer")
    a_agent = _call(a, texts=AGENT_SEGMENTS, agent_user_id=agent_id, title="A agent")
    b_customer = _call(b, texts=(f"my number is {CUSTOMER_PHONE}",), title="B customer")
    b_agent = _call(b, texts=AGENT_SEGMENTS, agent_user_id=agent_id, title="B agent")
    a_result = _scored(a, a_customer, a_version)
    _scored(a, a_agent, a_version)
    b_result = _scored(b, b_customer, b_version)
    _scored(b, b_agent, b_version)

    document, audit = _export(a, [CUSTOMER_PHONE, AGENT_EMAIL_REQUEST])
    assert {match["meeting_id"] for match in document["matches"]} == {a_customer["meeting_id"]}
    assert _listed_ids(document) == {a_agent["meeting_id"]}
    dumped = json.dumps(document)
    for value in (b_customer["meeting_id"], b_agent["meeting_id"], b_result):
        assert value not in dumped
    assert a_result in dumped  # allow: workspace A's own result
    assert audit["scorecard_results"] == 2 and audit["agent_subjects"] == 1


def test_a5_tombstoned_and_clause_excluded_agent_meetings_are_not_listed(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom354-a5")
    agent_id = _agent_user()
    version_id = _version(tenant, TWO_CRITERIA)
    live = _call(tenant, texts=AGENT_SEGMENTS, agent_user_id=agent_id, title="Live")
    tombstoned = _call(tenant, texts=AGENT_SEGMENTS, agent_user_id=agent_id, title="Gone")
    excluded = _call(tenant, texts=AGENT_SEGMENTS, agent_user_id=agent_id, title="Outside")
    for case in (live, tombstoned, excluded):
        _scored(tenant, case, version_id)
    with inspection_session_factory()() as db:
        db.get(Meeting, tombstoned["meeting_id"]).deleted_at = NOW
        db.commit()

    document, _audit = _export(
        tenant, [AGENT_EMAIL], meeting_clause=Meeting.id != excluded["meeting_id"]
    )
    assert _listed_ids(document) == {live["meeting_id"]}
    dumped = json.dumps(document)
    assert tombstoned["meeting_id"] not in dumped
    assert excluded["meeting_id"] not in dumped


def test_a10_the_agent_path_is_bounded_by_the_scan_limit(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom354-a10")
    agent_id = _agent_user()
    version_id = _version(tenant, TWO_CRITERIA)
    cases = [
        _call(
            tenant,
            texts=AGENT_SEGMENTS,
            agent_user_id=agent_id,
            title=f"Call {index}",
            created_at=NOW + timedelta(minutes=index),
        )
        for index in range(3)
    ]
    for case in cases:
        _scored(tenant, case, version_id)

    document, audit = _export(tenant, [AGENT_EMAIL], limit=2)
    agent = document["agent"]
    assert agent["meetings_scanned"] == 2 and agent["truncated"] is True
    # Newest first: the two latest of the three.
    assert _listed_ids(document) == {cases[2]["meeting_id"], cases[1]["meeting_id"]}
    assert audit["agent_meetings_scanned"] == 2 and audit["agent_truncated"] is True


# --- A6: redaction ------------------------------------------------------------------------


def test_a6_a_redacted_evidence_phrase_stays_redacted_and_no_segment_text_leaves(
    client, email_outbox, monkeypatch, settings
):
    # A real redacted meeting: the pipeline kept the encrypted originals of the
    # transcript values in pii_mappings under the configured key, so resolving a
    # placeholder is actually possible here.
    tenant, meeting_id = _redacted_meeting(client, email_outbox, monkeypatch, settings, "hom354-a6")
    agent_id = _agent_user()
    with inspection_session_factory()() as db:
        db.get(Meeting, meeting_id).agent_user_id = agent_id
        revision = db.scalar(
            select(TranscriptRevision).where(
                TranscriptRevision.meeting_id == meeting_id,
                TranscriptRevision.is_current.is_(True),
            )
        )
        mappings = list(
            db.scalars(
                select(PiiMapping).where(
                    PiiMapping.meeting_id == meeting_id,
                    PiiMapping.placeholder == "[PHONE_1]",
                )
            )
        )
        db.commit()
    # The premise: a real mapping row holds the encrypted original of [PHONE_1].
    assert mappings, "the pipeline kept no [PHONE_1] mapping"
    assert {mapping.key_id for mapping in mappings} == {"k1"}

    case = {"meeting_id": meeting_id, "transcript_revision_id": revision.id}
    result_id = _scored(tenant, case, _version(tenant, TWO_CRITERIA))
    # A criterion whose stored evidence cites the placeholder, as a check run on
    # the redacted turns would.
    _set_evidence(result_id, 0, [REDACTED_SPAN])

    document, _audit = _export(tenant, [AGENT_EMAIL_REQUEST])
    assert document["matches"] == []  # no transcript text holds the agent address
    (entry,) = document["agent"]["meetings"]
    (result,) = entry["scorecards"]
    assert result["criteria"][0]["evidence"] == [REDACTED_SPAN]
    # Allow-control: a non-redacted evidence phrase goes out unchanged.
    assert "goodbye" in json.dumps(result["criteria"][1]["evidence"])
    dumped = json.dumps(document, ensure_ascii=False)
    assert "[PHONE_1]" in dumped  # the placeholder itself is exported as stored
    for form in ORIGINAL_PHONE_FORMS:  # the original behind it never leaves
        assert form not in dumped, form
    assert ORIGINAL_EMAIL not in dumped
    for text in SEGMENTS:  # no transcript text of the agent path leaves
        assert text not in dumped


# --- A7, A8: shape and audit --------------------------------------------------------------


def test_a7_without_an_agent_subject_the_document_keeps_its_shape(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom354-a7")
    _call(tenant, texts=(f"my number is {CUSTOMER_PHONE}",))

    document, audit = _export(tenant, [CUSTOMER_PHONE])
    assert set(document) == {"generated_at", "workspace_id", "subject", "scope", "matches"}
    (match,) = document["matches"]
    assert set(match) == {
        "meeting_id",
        "title",
        "created_at",
        "retention_expires_at",
        "revisions",
        "summary",
        "actions",
        "scorecards",
    }
    assert match["scorecards"] == []
    assert "scorecard" in document["scope"]["notice"]
    assert audit["agent_subjects"] == 0
    assert audit["agent_meetings_scanned"] == 0
    assert audit["agent_truncated"] is False


def test_a8_the_audit_holds_the_counts_and_no_value(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom354-a8")
    agent_id = _agent_user()
    version_id = _version(tenant, TWO_CRITERIA)
    customer = _call(tenant, texts=(f"my number is {CUSTOMER_PHONE}",), title="Customer")
    agent_case = _call(tenant, texts=AGENT_SEGMENTS, agent_user_id=agent_id, title="Agent")
    _scored(tenant, customer, version_id)
    _scored(tenant, agent_case, version_id)

    response = client.post(
        "/api/v1/privacy/subject-exports",
        json={"identifiers": [CUSTOMER_PHONE, AGENT_EMAIL_REQUEST], "reason": "DSAR HOM-354"},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert response.status_code == 200, response.text
    with inspection_session_factory()() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == "subject.exported"))
        details = event.details
    assert details["scorecard_results"] == 2  # one on each path
    assert details["agent_subjects"] == 1
    assert details["agent_meetings_scanned"] == 1
    assert details["agent_truncated"] is False
    dumped = json.dumps(details)
    for value in (AGENT_EMAIL, AGENT_PHONE_E164, "09121234567", CUSTOMER_PHONE, agent_id):
        assert value not in dumped


# --- A9 (D7): the conversation purge -----------------------------------------------------


def test_a9_the_conversation_purge_removes_the_scorecards_from_the_export(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom354-a9")
    case = _call(tenant, texts=(f"my number is {CUSTOMER_PHONE}",))
    _scored(tenant, case, _version(tenant, TWO_CRITERIA))
    document, _audit = _export(tenant, [CUSTOMER_PHONE])
    (match,) = document["matches"]
    assert match["scorecards"]

    deleted = client.delete(
        f"/api/v1/meetings/{case['meeting_id']}",
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert deleted.status_code == 204, deleted.text
    assert _purge(case["meeting_id"]).state is PurgeState.COMPLETED

    document, _audit = _export(tenant, [CUSTOMER_PHONE])
    assert document["matches"] == []
    with inspection_session_factory()() as db:
        for model in (ScorecardResultRecord, CriterionResultRecord):
            count = db.scalar(
                select(func.count())
                .select_from(model)
                .where(model.meeting_id == case["meeting_id"])
            )
            assert count == 0, model


# --- A11: row-level security (PostgreSQL only) -------------------------------------------


@pytest.mark.postgres
def test_row_level_security_hides_another_workspaces_scorecards(client, email_outbox):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")
    a = _tenant(client, email_outbox, "hom354-rls-a")
    b = _tenant(client, email_outbox, "hom354-rls-b")
    agent_id = _agent_user()
    a_case = _call(
        a, texts=(f"my number is {CUSTOMER_PHONE}", AGENT_SEGMENTS[0]), agent_user_id=agent_id
    )
    _scored(a, a_case, _version(a, TWO_CRITERIA))

    # Bound to B, even A's own workspace id and identifiers find nothing.
    with _session(b) as db:
        document, audit = build_subject_export(
            db,
            workspace_id=a["workspace_id"],
            identifiers=[
                parse_subject_identifier(value) for value in (CUSTOMER_PHONE, AGENT_EMAIL)
            ],
            meeting_clause=true(),
            now=datetime.now(UTC),
        )
    assert document["matches"] == []
    assert "agent" not in document
    assert audit["meetings_scanned"] == 0
    assert audit["agent_subjects"] == 0
