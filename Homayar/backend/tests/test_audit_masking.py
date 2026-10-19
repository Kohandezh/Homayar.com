"""Write-time masking of personal data in free-text audit values (HOM-114, owner option B).

The owner chose (2026-10-17) append-only audit events plus write-time masking:
free text a user or an integration supplies (reasons, a key's name, integration
identifiers) goes through the text PII detector before it is stored, and is
capped at 120 characters. Ids, counts and enum values are stored unchanged.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.database.saas_models import AuditEvent
from backend.privacy.text_pii import valid_iban, valid_national_id
from backend.security.context import CSRF_COOKIE
from backend.services import audit_service
from backend.services.audit_service import MAX_FREE_TEXT_LENGTH
from backend.tests._database import inspection_session_factory
from backend.tests.test_share_policies import _cookies
from backend.tests.test_uploads_api import _authenticated_workspace


def record_audit_event(db, *, event_type, **fields):
    """Write with the target type the registry records for this event type."""
    from backend.services.audit_event_types import AuditEventType

    if "target_type" not in fields and event_type in set(AuditEventType):
        fields["target_type"] = AuditEventType(event_type).target_type
    return audit_service.record_audit_event(db, event_type=event_type, **fields)


def _national_id() -> str:
    return next(f"{n:010d}" for n in range(1_000_000_000, 1_000_001_000)
                if valid_national_id(f"{n:010d}"))


def _iban(bban: str = "0170000000110987654321") -> str:
    digits = "".join(str(int(c, 36)) for c in bban + "IR00")
    value = f"IR{98 - int(digits) % 97:02d}{bban}"
    assert valid_iban(value)
    return value


PHONE = "0912" + "1234567"
EMAIL = "ali.rezaei" + "@example.com"
NATIONAL_ID = _national_id()
IBAN = _iban()
PERSONAL = (PHONE, EMAIL, NATIONAL_ID, IBAN)
FREE_TEXT = f"caller {PHONE}, {EMAIL}, id {NATIONAL_ID}, iban {IBAN}"


def _stored(event_type: str) -> AuditEvent:
    with inspection_session_factory()() as db:
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.event_type == event_type)))
    assert len(events) == 1, events
    return events[0]


def _assert_masked(value: str) -> None:
    for raw in PERSONAL:
        assert raw not in value, raw
    for placeholder in ("[PHONE_1]", "[EMAIL_1]", "[IR_NATIONAL_ID_1]", "[IBAN_1]"):
        assert placeholder in value, placeholder
    assert len(value) <= MAX_FREE_TEXT_LENGTH


# Every free-text detail field the owner listed, with the exact key and the
# neighbouring ids/counts/enums its call site writes.
CASES = [
    ("pii.revealed", "reason",
     {"redaction_id": str(uuid.uuid4()), "placeholder": "[PHONE_1]", "pii_class": "phone"}),
    ("subject.exported", "reason",
     {"meetings": 3, "segments": 12, "identifier_classes": "phone,email"}),
    ("ai_policy.updated", "reason",
     {"version": 2, "previous_version": 1, "digest": "a" * 64, "stages": "asr,intelligence"}),
    ("transcript.revision.created", "reason",
     {"meeting_id": str(uuid.uuid4()), "revision_number": 2, "source": "correction",
      "segments_changed": 1}),
    ("media.legal_hold_released", "release_reason",
     {"media_id": str(uuid.uuid4()), "kind": "original", "state": "retained",
      "deletion_pending": False}),
    ("media.deletion_requested", "reason",
     {"media_id": str(uuid.uuid4()), "kind": "original", "state": "deletion_requested"}),
    ("media.deleted", "deletion_reason",
     {"media_id": str(uuid.uuid4()), "kind": "processed", "state": "deleted"}),
    ("api_key.created", "name",
     {"credential_prefix": "0123abcd", "scopes": "meeting:read",
      "expires_at": "2027-01-01T00:00:00+00:00"}),
    ("ingestion.accepted", "external_event_id",
     {"ingestion_id": str(uuid.uuid4()), "source_type": "api", "media_size_bytes": 4096}),
    ("ingestion.accepted", "source_provider",
     {"ingestion_id": str(uuid.uuid4()), "source_type": "pbx", "media_size_bytes": 4096}),
]


@pytest.mark.parametrize(("event_type", "key", "neighbours"), CASES,
                         ids=[f"{e}:{k}" for e, k, _ in CASES])
def test_free_text_fields_are_masked_and_ids_counts_enums_kept(
    client, settings, event_type, key, neighbours
):
    from backend.database.relational import get_session_factory

    with get_session_factory()() as db:
        record_audit_event(
            db, event_type=event_type, details={key: FREE_TEXT, **neighbours}, settings=settings
        )
        db.commit()
    details = _stored(event_type).details
    _assert_masked(details[key])
    for neighbour, value in neighbours.items():
        assert details[neighbour] == value, neighbour


def test_masking_happens_before_the_cap(client, settings):
    from backend.database.relational import get_session_factory

    long_reason = "x" * (MAX_FREE_TEXT_LENGTH - 5) + f" {PHONE}"
    with get_session_factory()() as db:
        record_audit_event(db, event_type="pii.revealed", details={"reason": long_reason},
                           settings=settings)
        db.commit()
    stored = _stored("pii.revealed").details["reason"]
    assert len(stored) == MAX_FREE_TEXT_LENGTH
    assert "0912" not in stored  # no half number survives the cut


def test_ids_inside_free_text_and_plain_reasons_are_untouched(client, settings):
    from backend.database.relational import get_session_factory

    # A fixed id: about 0.2% of random UUIDs hold a digit run the detector reads as personal
    # data, which made this test fail at random.
    plain = "retry after 3f2b9c1e-7a4d-4e8b-b5c6-d1e2f3a4b5c6 (job 1234)"
    with get_session_factory()() as db:
        record_audit_event(db, event_type="meeting.purge_completed",
                           details={"reason": plain, "attempts": 2}, settings=settings)
        db.commit()
    details = _stored("meeting.purge_completed").details
    assert details == {"reason": plain, "attempts": 2}


def test_api_key_name_is_masked_end_to_end(client, email_outbox):
    _, workspace_id = _authenticated_workspace(client, email_outbox)
    cookies = _cookies(client)
    headers = {"X-Workspace-ID": workspace_id, "X-CSRF-Token": cookies[CSRF_COOKIE]}
    created = client.post(
        "/api/v1/api-keys",
        json={"name": f"pbx {PHONE}", "scopes": ["meeting:read"], "expires_in_days": 30},
        headers=headers,
        cookies=cookies,
    )
    assert created.status_code == 201, created.text
    event = _stored("api_key.created")
    assert event.details["name"] == "pbx [PHONE_1]"
    assert event.details["credential_prefix"] == created.json()["key"].split(".")[0][3:]


def test_subject_export_reason_is_masked_end_to_end(client, email_outbox):
    _, workspace_id = _authenticated_workspace(client, email_outbox)
    cookies = _cookies(client)
    response = client.post(
        "/api/v1/privacy/subject-exports",
        json={"identifiers": [PHONE], "reason": f"request from {EMAIL} about {PHONE}"},
        headers={"X-Workspace-ID": workspace_id, "X-CSRF-Token": cookies[CSRF_COOKIE]},
        cookies=cookies,
    )
    assert response.status_code == 200, response.text
    reason = _stored("subject.exported").details["reason"]
    assert reason == "request from [EMAIL_1] about [PHONE_1]"
