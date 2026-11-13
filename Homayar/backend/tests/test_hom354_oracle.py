"""HOM-354 blind oracle: the subject export carries the HOM-56 scorecard results.

Written from SPEC A1 to A11 before the implementation.  Synthetic data only.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, inspect, select, true

from backend.conversation_checks import CHECKS_VERSION
from backend.database.saas_models import (
    ActionItem,
    AuditEvent,
    CriterionResultRecord,
    Meeting,
    MeetingSummary,
    PiiMapping,
    PurgeState,
    ScorecardResultRecord,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptSource,
    User,
)
from backend.privacy.subject_export import build_subject_export, parse_subject_identifier
from backend.scorecards import SCORECARDS_VERSION
from backend.tests._database import inspection_session_factory
from backend.tests.test_free_text_partial_pii import _redacted_meeting
from backend.tests.test_ingestion_ledger import _headers, _tenant
from backend.tests.test_privacy_routing import SEGMENTS, _no_raw
from backend.tests.test_retention_purge import _purge
from backend.tests.test_scorecard_result_service import _session, _version

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
AGENT_EMAIL = "hom354.agent@example.org"
AGENT_PHONE = "+989121234567"
AGENT_PHONE_CANONICAL = "09121234567"
CUSTOMER_PHONE = "09351112233"
CUSTOMER_PHONE_SPOKEN = "0935 111 2233"
BLOB_MARKER = "BLOB-HOM354"
DEFINITION_DIGEST = hashlib.sha256(b"hom354-definition").hexdigest()

SPAN_PLACEHOLDER = {
    "turn_index": 1,
    "speaker_role": "customer",
    "start_ms": 1000,
    "end_ms": 1900,
    "phrase": "[PHONE_1]",
    "token_start": 3,
    "token_end": 4,
}
SPAN_HELLO = {
    "turn_index": 0,
    "speaker_role": "agent",
    "start_ms": 0,
    "end_ms": 900,
    "phrase": "hello",
    "token_start": 0,
    "token_end": 1,
}
SPAN_CITATION = {
    "turn_index": 2,
    "speaker_role": "agent",
    "start_ms": 2000,
    "end_ms": 2900,
    "phrase": None,
    "token_start": None,
    "token_end": None,
}
CRITERIA = [
    {
        "position": 0,
        "criterion_id": "greeting",
        "weight": 50,
        "auto_fail": False,
        "na_allowed": False,
        "outcome": "passed",
        "reason": None,
        "decided_by": "rule",
        "review_required": False,
        "review_reasons": [],
        "check_outcome": "passed",
        "check_reason": None,
        "evidence": [SPAN_PLACEHOLDER, SPAN_HELLO],
        "candidate_evidence": [],
    },
    {
        "position": 1,
        "criterion_id": "fee_explained",
        "weight": 25,
        "auto_fail": True,
        "na_allowed": True,
        "outcome": "insufficient_evidence",
        "reason": "review_required",
        "decided_by": None,
        "review_required": True,
        "review_reasons": ["low_confidence"],
        "check_outcome": "not_computable",
        "check_reason": "evaluator_unavailable",
        "evidence": [],
        "candidate_evidence": [SPAN_CITATION],
    },
]
PASSED = {
    "conversation_type": "call",
    "direction": "inbound",
    "outcome": "passed",
    "reason": None,
    "complete": True,
    "pass_threshold": 60,
    "applicable_weight": 75,
    "earned_weight": 50,
    "failed_weight": 25,
    "insufficient_weight": 0,
}
INCOMPLETE = {
    "conversation_type": "call",
    "direction": None,
    "outcome": "insufficient_evidence",
    "reason": "evidence_missing",
    "complete": False,
    "pass_threshold": 80,
    "applicable_weight": 100,
    "earned_weight": 25,
    "failed_weight": 50,
    "insufficient_weight": 25,
}
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
BASE_AUDIT_KEYS = {
    "identifier_classes",
    "identifier_count",
    "meetings_scanned",
    "meetings_matched",
    "segments_matched",
    "truncated",
}
NEW_AUDIT_KEYS = {
    "scorecard_results",
    "agent_subjects",
    "agent_meetings_scanned",
    "agent_truncated",
}


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def settings(settings):
    return settings.model_copy(
        update={"pii_mapping_keys": f"k1:{_key()}", "pii_mapping_active_key_id": "k1"}
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _user(email: str | None = None, phone: str | None = None) -> str:
    with inspection_session_factory()() as db:
        user = User(email=email, phone_e164=phone, display_name="Synthetic agent")
        db.add(user)
        db.commit()
        return user.id


def _texts(title: str, customer: bool) -> list[str]:
    middle = (
        f"{title}: my number is {CUSTOMER_PHONE_SPOKEN}, please call back"
        if customer
        else f"{title}: I want to change my plan"
    )
    return [f"{title}: hello and welcome", middle, f"{title}: thank you, goodbye"]


def _meeting(
    tenant: dict,
    title: str,
    *,
    agent: str | None = None,
    customer: bool = False,
    age_minutes: int = 0,
    deleted: bool = False,
) -> dict[str, Any]:
    texts = _texts(title, customer)
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=tenant["workspace_id"],
            created_by_user_id=tenant["user_id"],
            agent_user_id=agent,
            title=title,
            conversation_type="call",
            direction="inbound",
            created_at=NOW - timedelta(minutes=age_minutes),
            deleted_at=NOW if deleted else None,
        )
        db.add(meeting)
        db.flush()
        revision = TranscriptRevision(
            workspace_id=tenant["workspace_id"],
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
        segments = [
            TranscriptSegment(
                workspace_id=tenant["workspace_id"],
                meeting_id=meeting.id,
                revision_id=revision.id,
                sequence=index,
                speaker_key="spk_0",
                start_ms=index * 1000,
                end_ms=index * 1000 + 900,
                text=text,
            )
            for index, text in enumerate(texts)
        ]
        db.add_all(segments)
        db.commit()
        return {
            "meeting_id": meeting.id,
            "revision_id": revision.id,
            "segment_ids": [segment.id for segment in segments],
            "texts": texts,
            "title": title,
        }


def _result(
    workspace_id: str,
    case: dict[str, Any],
    version_id: str,
    *,
    tag: str = "r",
    columns: dict[str, Any] = PASSED,
    criteria: list[dict[str, Any]] = CRITERIA,
    created_at: datetime = NOW,
) -> str:
    with inspection_session_factory()() as db:
        row = ScorecardResultRecord(
            workspace_id=workspace_id,
            meeting_id=case["meeting_id"],
            scorecard_version_id=version_id,
            transcript_revision_id=case["revision_id"],
            scorecards_version=SCORECARDS_VERSION,
            checks_version=CHECKS_VERSION,
            definition_digest=DEFINITION_DIGEST,
            input_digest=hashlib.sha256(f"{case['meeting_id']}:{tag}".encode()).hexdigest(),
            check_run={"marker": f"CHECK-RUN-{BLOB_MARKER}"},
            result={"marker": f"RESULT-{BLOB_MARKER}"},
            created_at=created_at,
            **columns,
        )
        db.add(row)
        db.flush()
        for criterion in reversed(criteria):
            db.add(
                CriterionResultRecord(
                    workspace_id=workspace_id,
                    meeting_id=case["meeting_id"],
                    result_id=row.id,
                    created_at=created_at,
                    **copy.deepcopy(criterion),
                )
            )
        db.commit()
        return row.id


def _expected(result_id: str) -> dict[str, Any]:
    with inspection_session_factory()() as db:
        row = db.get(ScorecardResultRecord, result_id)
        criteria = list(
            db.scalars(
                select(CriterionResultRecord)
                .where(CriterionResultRecord.result_id == result_id)
                .order_by(CriterionResultRecord.position)
            )
        )
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
            "created_at": _iso(row.created_at),
            "criteria": [
                {key: getattr(criterion, key) for key in sorted(CRITERION_KEYS)}
                for criterion in criteria
            ],
        }


def _agent_meeting(case: dict[str, Any], result_ids: list[str]) -> dict[str, Any]:
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, case["meeting_id"])
        return {
            "meeting_id": meeting.id,
            "title": meeting.title,
            "created_at": _iso(meeting.created_at),
            "retention_expires_at": _iso(meeting.retention_expires_at),
            "scorecards": [_expected(result_id) for result_id in result_ids],
        }


def _export(
    workspace_id: str,
    identifiers: list[str],
    *,
    clause: Any = None,
    limit: int | None = None,
    db: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    arguments: dict[str, Any] = {
        "workspace_id": workspace_id,
        "identifiers": [parse_subject_identifier(value) for value in identifiers],
        "meeting_clause": true() if clause is None else clause,
        "now": NOW,
    }
    if limit is not None:
        arguments["limit"] = limit
    if db is not None:
        return build_subject_export(db, **arguments)
    with inspection_session_factory()() as session:
        return build_subject_export(session, **arguments)


def _http_export(client, tenant: dict, identifiers: list[str]) -> dict[str, Any]:
    response = client.post(
        "/api/v1/privacy/subject-exports",
        json={"identifiers": identifiers, "reason": "DSAR HOM-354"},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert response.status_code == 200, response.text
    return response.json()


def _dump(document: Any) -> str:
    return json.dumps(document, ensure_ascii=False, default=str)


def _matches(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {match["meeting_id"]: match for match in document["matches"]}


def _agent_ids(document: dict[str, Any]) -> list[str]:
    return [meeting["meeting_id"] for meeting in document["agent"]["meetings"]]


def _owner_email(tenant: dict) -> str:
    with inspection_session_factory()() as db:
        return db.get(User, tenant["user_id"]).email


def _subject_audits() -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(select(AuditEvent).where(AuditEvent.event_type == "subject.exported"))
        )


def _row_dump(row: AuditEvent) -> str:
    return _dump(
        {
            attribute.key: getattr(row, attribute.key)
            for attribute in inspect(AuditEvent).column_attrs
        }
    )


def _rows_for(meeting_id: str) -> tuple[int, int]:
    with inspection_session_factory()() as db:
        return (
            db.scalar(
                select(func.count())
                .select_from(ScorecardResultRecord)
                .where(ScorecardResultRecord.meeting_id == meeting_id)
            ),
            db.scalar(
                select(func.count())
                .select_from(CriterionResultRecord)
                .where(CriterionResultRecord.meeting_id == meeting_id)
            ),
        )


def test_a1_a_matched_meeting_lists_its_result_with_every_stored_column(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom354-a1")
    case = _meeting(tenant, "Customer call", customer=True)
    version_id = _version(tenant)
    result_id = _result(tenant["workspace_id"], case, version_id)

    document, _ = _export(tenant["workspace_id"], [CUSTOMER_PHONE])

    (match,) = document["matches"]
    assert match["meeting_id"] == case["meeting_id"]
    (exported,) = match["scorecards"]
    assert set(exported) == RESULT_KEYS
    assert exported == _expected(result_id)
    assert exported["result_id"] == result_id
    assert exported["scorecard_version_id"] == version_id
    assert exported["transcript_revision_id"] == case["revision_id"]
    assert (exported["scorecards_version"], exported["checks_version"]) == (
        SCORECARDS_VERSION,
        CHECKS_VERSION,
    )
    assert {key: exported[key] for key in PASSED} == PASSED
    assert [criterion["position"] for criterion in exported["criteria"]] == [0, 1]
    for criterion, stored in zip(exported["criteria"], CRITERIA, strict=True):
        assert set(criterion) == CRITERION_KEYS
        assert criterion == stored
    assert exported["criteria"][0]["evidence"] == [SPAN_PLACEHOLDER, SPAN_HELLO]
    assert exported["criteria"][1]["candidate_evidence"] == [SPAN_CITATION]
    dumped = _dump(document)
    assert BLOB_MARKER not in dumped
    assert DEFINITION_DIGEST not in dumped
    assert hashlib.sha256(f"{case['meeting_id']}:r".encode()).hexdigest() not in dumped


def test_a1_results_are_oldest_first_and_criteria_are_by_position(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom354-order")
    case = _meeting(tenant, "Customer call", customer=True)
    version_id = _version(tenant)
    workspace_id = tenant["workspace_id"]
    late = _result(
        workspace_id,
        case,
        version_id,
        tag="late",
        columns=INCOMPLETE,
        created_at=NOW + timedelta(minutes=5),
    )
    tied = sorted(
        _result(workspace_id, case, version_id, tag=tag, created_at=NOW) for tag in ("t1", "t2")
    )

    document, _ = _export(workspace_id, [CUSTOMER_PHONE])

    (match,) = document["matches"]
    assert [result["result_id"] for result in match["scorecards"]] == [*tied, late]
    assert match["scorecards"] == [_expected(result_id) for result_id in [*tied, late]]
    assert {key: match["scorecards"][-1][key] for key in INCOMPLETE} == INCOMPLETE
    for result in match["scorecards"]:
        assert [criterion["criterion_id"] for criterion in result["criteria"]] == [
            "greeting",
            "fee_explained",
        ]


def test_a2_no_result_gives_an_empty_list_and_results_alone_never_match(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom354-a2")
    workspace_id = tenant["workspace_id"]
    version_id = _version(tenant)
    empty = _meeting(tenant, "Matched without result", customer=True, age_minutes=3)
    scored = _meeting(tenant, "Matched with result", customer=True, age_minutes=2)
    unmatched = _meeting(tenant, "Unmatched with result", age_minutes=1)
    scored_id = _result(workspace_id, scored, version_id)
    quoting = copy.deepcopy(CRITERIA)
    quoting[0]["evidence"] = [{**SPAN_HELLO, "phrase": CUSTOMER_PHONE_SPOKEN}]
    unmatched_id = _result(workspace_id, unmatched, version_id, criteria=quoting)

    document, audit = _export(workspace_id, [CUSTOMER_PHONE])

    assert [match["meeting_id"] for match in document["matches"]] == [
        scored["meeting_id"],
        empty["meeting_id"],
    ]
    matches = _matches(document)
    assert matches[empty["meeting_id"]]["scorecards"] == []
    assert matches[scored["meeting_id"]]["scorecards"] == [_expected(scored_id)]
    dumped = _dump(document)
    assert unmatched["meeting_id"] not in dumped
    assert unmatched_id not in dumped
    assert audit["meetings_matched"] == 2
    assert audit["scorecard_results"] == 1


def test_a7_without_an_agent_subject_the_document_keeps_its_shape(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom354-a7")
    case = _meeting(tenant, "Customer call", customer=True)

    document, audit = _export(tenant["workspace_id"], [CUSTOMER_PHONE])

    assert "agent" not in document
    assert set(document) == {"generated_at", "workspace_id", "subject", "scope", "matches"}
    assert set(document["scope"]) == {"detector_version", "meetings_scanned", "truncated", "notice"}
    with inspection_session_factory()() as db:
        created_at = _iso(db.get(Meeting, case["meeting_id"]).created_at)
    assert document["matches"] == [
        {
            "meeting_id": case["meeting_id"],
            "title": case["title"],
            "created_at": created_at,
            "retention_expires_at": None,
            "revisions": [
                {
                    "revision_id": case["revision_id"],
                    "revision_number": 1,
                    "is_current": True,
                    "source": "asr",
                    "segments": [
                        {
                            "segment_id": case["segment_ids"][1],
                            "sequence": 1,
                            "speaker": "spk_0",
                            "start_ms": 1000,
                            "end_ms": 1900,
                            "text": case["texts"][1],
                            "matched": ["phone"],
                        }
                    ],
                }
            ],
            "summary": [],
            "actions": [],
            "scorecards": [],
        }
    ]
    assert set(audit) == BASE_AUDIT_KEYS | NEW_AUDIT_KEYS
    assert {key: audit[key] for key in NEW_AUDIT_KEYS} == {
        "scorecard_results": 0,
        "agent_subjects": 0,
        "agent_meetings_scanned": 0,
        "agent_truncated": False,
    }
    assert (audit["meetings_scanned"], audit["meetings_matched"], audit["segments_matched"]) == (
        1,
        1,
        1,
    )


def test_d6_the_notice_states_the_new_scope_and_exclusions(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom354-notice")

    document, _ = _export(tenant["workspace_id"], [CUSTOMER_PHONE])

    notice = document["scope"]["notice"].casefold()
    assert "audio" in notice
    assert "scorecard" in notice
    for excluded in ("coaching", "appeal", "calibration"):
        assert excluded in notice, excluded


AGENT_SPELLINGS = [
    pytest.param(AGENT_EMAIL, "Hom354.Agent@Example.ORG", id="email-requested-in-another-case"),
    pytest.param("Hom354.Mixed@Example.ORG", "hom354.mixed@example.org", id="email-stored-mixed"),
    pytest.param(AGENT_EMAIL, "0912 123 4567", id="phone-national-spaced"),
    pytest.param(AGENT_EMAIL, "+98 912 123 4567", id="phone-international"),
    pytest.param(AGENT_EMAIL, "۰۹۱۲ ۱۲۳ ۴۵۶۷", id="phone-persian-digits"),
]


@pytest.mark.parametrize(("stored_email", "requested"), AGENT_SPELLINGS)
def test_a3_an_agent_is_found_through_the_account_identifier(
    client, email_outbox, stored_email, requested
):
    tenant = _tenant(client, email_outbox, "hom354-a3")
    workspace_id = tenant["workspace_id"]
    version_id = _version(tenant)
    agent = _user(stored_email, AGENT_PHONE)
    older = _meeting(tenant, "Agent call one", agent=agent, age_minutes=3)
    newer = _meeting(tenant, "Agent call two", agent=agent, age_minutes=2)
    unscored = _meeting(tenant, "Agent call unscored", agent=agent, age_minutes=1)
    other = _meeting(tenant, "Unassigned call", age_minutes=4)
    older_id = _result(workspace_id, older, version_id)
    newer_ids = [
        _result(workspace_id, newer, version_id, tag="a", created_at=NOW),
        _result(
            workspace_id,
            newer,
            version_id,
            tag="b",
            columns=INCOMPLETE,
            created_at=NOW + timedelta(minutes=1),
        ),
    ]
    other_id = _result(workspace_id, other, version_id)

    document = _http_export(client, tenant, [requested])

    assert document["matches"] == []
    assert "agent" in document
    assert set(document["agent"]) == {"meetings_scanned", "truncated", "meetings"}
    assert document["agent"]["truncated"] is False
    assert document["agent"]["meetings"] == [
        _agent_meeting(newer, newer_ids),
        _agent_meeting(older, [older_id]),
    ]
    dumped = _dump(document)
    for absent in (unscored["meeting_id"], other["meeting_id"], other_id, agent, AGENT_PHONE):
        assert absent not in dumped, absent
    shown = parse_subject_identifier(requested).value
    for value in (stored_email, stored_email.casefold(), AGENT_PHONE_CANONICAL):
        if value != shown:
            assert value not in dumped, value
    for case in (older, newer, unscored):
        for text in case["texts"]:
            assert text not in dumped, text


def test_a4_deny_only_an_agent_of_this_workspace_is_an_agent_subject(client, email_outbox):
    a = _tenant(client, email_outbox, "hom354-subject-a")
    b = _tenant(client, email_outbox, "hom354-subject-b")
    a_version, b_version = _version(a), _version(b)
    agent = _user(AGENT_EMAIL, AGENT_PHONE)
    only_in_b = _user("hom354.only-b@example.org")
    idle = _user("hom354.idle@example.org")
    _user("hom354.unassigned@example.org")
    broken = _user("no-at-sign")
    a_case = _meeting(a, "Agent call", agent=agent, age_minutes=2)
    a_id = _result(a["workspace_id"], a_case, a_version)
    _meeting(a, "Idle agent call", agent=idle, age_minutes=3)
    broken_case = _meeting(a, "Broken account call", agent=broken, age_minutes=4)
    _result(a["workspace_id"], broken_case, a_version)
    b_case = _meeting(b, "Agent call in B", agent=only_in_b)
    b_id = _result(b["workspace_id"], b_case, b_version)

    for denied in (_owner_email(a), "hom354.only-b@example.org", "hom354.unassigned@example.org"):
        document, audit = _export(a["workspace_id"], [denied])
        assert "agent" not in document, denied
        assert audit["agent_subjects"] == 0, denied
        assert b_id not in _dump(document)

    document, audit = _export(a["workspace_id"], [AGENT_EMAIL])
    assert document["agent"]["meetings"] == [_agent_meeting(a_case, [a_id])]
    assert audit["agent_subjects"] == 1

    document, audit = _export(a["workspace_id"], ["hom354.idle@example.org"])
    assert document["agent"]["meetings"] == []
    assert audit["agent_subjects"] == 1

    document, audit = _export(b["workspace_id"], ["hom354.only-b@example.org"])
    assert document["agent"]["meetings"] == [_agent_meeting(b_case, [b_id])]
    assert audit["agent_subjects"] == 1


def _two_workspaces(client, email_outbox, label: str) -> dict[str, Any]:
    a = _tenant(client, email_outbox, f"{label}-a")
    b = _tenant(client, email_outbox, f"{label}-b")
    a_version, b_version = _version(a), _version(b)
    agent = _user(AGENT_EMAIL, AGENT_PHONE)
    a_case = _meeting(a, "Call in A", agent=agent, customer=True)
    b_case = _meeting(b, "Call in B", agent=agent, customer=True)
    a_id = _result(a["workspace_id"], a_case, a_version)
    b_id = _result(b["workspace_id"], b_case, b_version)
    stray_id = _result(b["workspace_id"], a_case, b_version, tag="stray")
    return {
        "a": a,
        "b": b,
        "a_case": a_case,
        "b_case": b_case,
        "a_id": a_id,
        "b_id": b_id,
        "stray_id": stray_id,
    }


def _assert_only_a(document: dict[str, Any], setup: dict[str, Any]) -> None:
    a_case, a_id = setup["a_case"], setup["a_id"]
    assert [match["meeting_id"] for match in document["matches"]] == [a_case["meeting_id"]]
    assert document["matches"][0]["scorecards"] == [_expected(a_id)]
    assert document["agent"]["meetings"] == [_agent_meeting(a_case, [a_id])]
    dumped = _dump(document)
    for absent in (
        setup["b_case"]["meeting_id"],
        setup["b_case"]["revision_id"],
        setup["b_id"],
        setup["stray_id"],
    ):
        assert absent not in dumped, absent


def test_a4_another_workspace_never_appears_through_the_workspace_filter(client, email_outbox):
    setup = _two_workspaces(client, email_outbox, "hom354-filter")

    document, audit = _export(setup["a"]["workspace_id"], [CUSTOMER_PHONE, AGENT_EMAIL])

    _assert_only_a(document, setup)
    assert audit["scorecard_results"] == 2
    document, _ = _export(setup["b"]["workspace_id"], [CUSTOMER_PHONE, AGENT_EMAIL])
    assert document["agent"]["meetings"] == [_agent_meeting(setup["b_case"], [setup["b_id"]])]
    assert setup["a_id"] not in _dump(document)


def test_a4_another_workspace_never_appears_through_the_route(client, email_outbox):
    setup = _two_workspaces(client, email_outbox, "hom354-route")

    document = _http_export(client, setup["a"], [CUSTOMER_PHONE_SPOKEN, "Hom354.Agent@Example.ORG"])

    _assert_only_a(document, setup)


@pytest.mark.postgres
def test_a11_row_level_security_keeps_the_export_in_the_bound_workspace(client, email_outbox):
    setup = _two_workspaces(client, email_outbox, "hom354-rls")
    a, b = setup["a"], setup["b"]

    with _session(a) as db:
        document, _ = _export(a["workspace_id"], [CUSTOMER_PHONE, AGENT_EMAIL], db=db)
    _assert_only_a(document, setup)

    with _session(b) as db:
        document, audit = _export(a["workspace_id"], [CUSTOMER_PHONE, AGENT_EMAIL], db=db)
    assert document["matches"] == []
    assert "agent" not in document
    assert audit["scorecard_results"] == 0
    dumped = _dump(document)
    for absent in (setup["a_id"], setup["b_id"], setup["stray_id"]):
        assert absent not in dumped


def test_a5_tombstoned_and_out_of_scope_meetings_are_not_listed(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom354-a5")
    workspace_id = tenant["workspace_id"]
    version_id = _version(tenant)
    agent = _user(AGENT_EMAIL, AGENT_PHONE)
    live = _meeting(tenant, "Live call", agent=agent, customer=True, age_minutes=3)
    tombstoned = _meeting(
        tenant, "Tombstoned call", agent=agent, customer=True, age_minutes=2, deleted=True
    )
    hidden = _meeting(tenant, "Hidden call", agent=agent, customer=True, age_minutes=1)
    live_id = _result(workspace_id, live, version_id)
    tombstoned_id = _result(workspace_id, tombstoned, version_id)
    hidden_id = _result(workspace_id, hidden, version_id)

    document, _ = _export(workspace_id, [AGENT_EMAIL, CUSTOMER_PHONE])
    assert _agent_ids(document) == [hidden["meeting_id"], live["meeting_id"]]
    assert list(_matches(document)) == [hidden["meeting_id"], live["meeting_id"]]
    assert tombstoned_id not in _dump(document)

    document, _ = _export(
        workspace_id,
        [AGENT_EMAIL, CUSTOMER_PHONE],
        clause=Meeting.id != hidden["meeting_id"],
    )
    assert document["agent"]["meetings"] == [_agent_meeting(live, [live_id])]
    assert list(_matches(document)) == [live["meeting_id"]]
    assert _matches(document)[live["meeting_id"]]["scorecards"] == [_expected(live_id)]
    dumped = _dump(document)
    for absent in (
        tombstoned["meeting_id"],
        tombstoned_id,
        hidden["meeting_id"],
        hidden_id,
    ):
        assert absent not in dumped, absent


def test_a6_placeholders_stay_and_no_transcript_text_reaches_the_agent_path(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _redacted_meeting(
        client, email_outbox, monkeypatch, settings, "hom354-redacted"
    )
    agent = _user(AGENT_EMAIL, AGENT_PHONE)
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        meeting.agent_user_id = agent
        db.commit()
        revision_id = db.scalar(
            select(TranscriptRevision.id)
            .where(TranscriptRevision.meeting_id == meeting_id)
            .order_by(TranscriptRevision.revision_number)
        )
        mapping = db.scalar(
            select(PiiMapping).where(
                PiiMapping.meeting_id == meeting_id, PiiMapping.placeholder == "[PHONE_1]"
            )
        )
        assert mapping is not None
        stored_texts = [
            *db.scalars(
                select(TranscriptSegment.text).where(TranscriptSegment.meeting_id == meeting_id)
            ),
            *db.scalars(
                select(MeetingSummary.overview).where(MeetingSummary.meeting_id == meeting_id)
            ),
            *db.scalars(select(ActionItem.description).where(ActionItem.meeting_id == meeting_id)),
        ]
    case = {"meeting_id": meeting_id, "revision_id": revision_id}
    result_id = _result(tenant["workspace_id"], case, _version(tenant))

    document = _http_export(client, tenant, [AGENT_EMAIL])

    (listed,) = document["agent"]["meetings"]
    assert listed["meeting_id"] == meeting_id
    assert listed["scorecards"] == [_expected(result_id)]
    evidence = listed["scorecards"][0]["criteria"][0]["evidence"]
    assert evidence == [SPAN_PLACEHOLDER, SPAN_HELLO]
    assert evidence[0]["phrase"] == "[PHONE_1]"
    assert evidence[1]["phrase"] == "hello"
    assert document["matches"] == []
    dumped = _dump(document)
    assert _no_raw(dumped)
    for text in [*SEGMENTS, *(text for text in stored_texts if text and len(text) >= 12)]:
        assert text not in dumped, text

    document = _http_export(client, tenant, ["09123456789"])
    (match,) = document["matches"]
    assert match["scorecards"] == [_expected(result_id)]
    assert match["scorecards"][0]["criteria"][0]["evidence"][0]["phrase"] == "[PHONE_1]"


def test_a8_the_audit_holds_the_new_counts_and_no_value(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom354-a8")
    workspace_id = tenant["workspace_id"]
    version_id = _version(tenant)
    agent = _user(AGENT_EMAIL, AGENT_PHONE)
    customer = _meeting(tenant, "Customer call", customer=True, age_minutes=3)
    first = _meeting(tenant, "Agent call one", agent=agent, age_minutes=2)
    second = _meeting(tenant, "Agent call two", agent=agent, age_minutes=1)
    _result(workspace_id, customer, version_id)
    _result(workspace_id, first, version_id, tag="a")
    _result(workspace_id, first, version_id, tag="b", created_at=NOW + timedelta(minutes=1))
    _result(workspace_id, second, version_id)
    requested = [CUSTOMER_PHONE_SPOKEN, "Hom354.Agent@Example.ORG", "0912 123 4567"]

    document = _http_export(client, tenant, requested)

    exported = sum(len(match["scorecards"]) for match in document["matches"]) + sum(
        len(meeting["scorecards"]) for meeting in document["agent"]["meetings"]
    )
    assert exported == 4
    (row,) = _subject_audits()
    assert {key: row.details.get(key) for key in NEW_AUDIT_KEYS} == {
        "scorecard_results": 4,
        "agent_subjects": 1,
        "agent_meetings_scanned": 2,
        "agent_truncated": False,
    }
    assert row.details["meetings_matched"] == 1
    dumped = _row_dump(row)
    for value in (
        *requested,
        AGENT_EMAIL,
        AGENT_PHONE,
        AGENT_PHONE_CANONICAL,
        CUSTOMER_PHONE,
        agent,
        _owner_email(tenant),
    ):
        assert value not in dumped, value

    _http_export(client, tenant, [CUSTOMER_PHONE])
    (later,) = [audit for audit in _subject_audits() if audit.id != row.id]
    assert {key: later.details.get(key) for key in NEW_AUDIT_KEYS} == {
        "scorecard_results": 1,
        "agent_subjects": 0,
        "agent_meetings_scanned": 0,
        "agent_truncated": False,
    }
    dumped = _row_dump(later)
    for value in (CUSTOMER_PHONE, AGENT_EMAIL, agent):
        assert value not in dumped, value


def test_a9_after_the_conversation_purge_the_export_lists_nothing_of_it(client, email_outbox):
    tenant = _tenant(client, email_outbox, "hom354-a9")
    workspace_id = tenant["workspace_id"]
    version_id = _version(tenant)
    agent = _user(AGENT_EMAIL, AGENT_PHONE)
    purged = _meeting(tenant, "Purged call", agent=agent, customer=True, age_minutes=2)
    kept = _meeting(tenant, "Kept call", agent=agent, customer=True, age_minutes=1)
    purged_id = _result(workspace_id, purged, version_id)
    kept_id = _result(workspace_id, kept, version_id)
    identifiers = [CUSTOMER_PHONE, AGENT_EMAIL]

    before = _http_export(client, tenant, identifiers)
    assert _matches(before)[purged["meeting_id"]]["scorecards"] == [_expected(purged_id)]
    assert _agent_ids(before) == [kept["meeting_id"], purged["meeting_id"]]
    assert _rows_for(purged["meeting_id"]) == (1, 2)

    deleted = client.delete(
        f"/api/v1/meetings/{purged['meeting_id']}",
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert deleted.status_code == 204, deleted.text
    assert _purge(purged["meeting_id"]).state is PurgeState.COMPLETED

    after = _http_export(client, tenant, identifiers)
    assert list(_matches(after)) == [kept["meeting_id"]]
    assert _matches(after)[kept["meeting_id"]]["scorecards"] == [_expected(kept_id)]
    assert after["agent"]["meetings"] == [_agent_meeting(kept, [kept_id])]
    dumped = _dump(after)
    assert purged["meeting_id"] not in dumped
    assert purged_id not in dumped
    assert _rows_for(purged["meeting_id"]) == (0, 0)
    assert _rows_for(kept["meeting_id"]) == (1, 2)


@pytest.mark.parametrize(
    ("limit", "scanned", "truncated", "listed"),
    [
        pytest.param(2, 2, True, 2, id="limit-below-the-agent-meetings"),
        pytest.param(3, 3, False, 3, id="limit-equal-to-the-agent-meetings"),
    ],
)
def test_a10_the_agent_path_is_bounded_by_the_limit(
    client, email_outbox, limit, scanned, truncated, listed
):
    tenant = _tenant(client, email_outbox, "hom354-a10")
    workspace_id = tenant["workspace_id"]
    version_id = _version(tenant)
    agent = _user(AGENT_EMAIL, AGENT_PHONE)
    cases = [
        _meeting(tenant, f"Agent call {age}", agent=agent, age_minutes=age) for age in (1, 2, 3)
    ]
    result_ids = [_result(workspace_id, case, version_id) for case in cases]

    document, audit = _export(workspace_id, [AGENT_EMAIL], limit=limit)

    assert document["agent"]["meetings_scanned"] == scanned
    assert document["agent"]["truncated"] is truncated
    assert document["agent"]["meetings"] == [
        _agent_meeting(case, [result_id])
        for case, result_id in list(zip(cases, result_ids, strict=True))[:listed]
    ]
    assert (audit["agent_meetings_scanned"], audit["agent_truncated"]) == (scanned, truncated)
    assert audit["scorecard_results"] == listed
