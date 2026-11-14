from __future__ import annotations

import ast
import hashlib
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    create_engine,
    delete,
    event,
    inspect,
    select,
    text,
)
from sqlalchemy.exc import DBAPIError, IntegrityError

from backend.backup.reconcile import CONTENT_TABLES as RECONCILED_TABLES
from backend.database.model_registry import Base
from backend.database.relational import get_session_factory
from backend.database.rls import (
    APP_ROLE,
    APPEND_ONLY_TABLES,
    NO_DELETE_TABLES,
    NO_UPDATE_TABLES,
    POLICIES,
    POLICY_NAME,
    SHARED_CONTENT_TABLES,
    TENANT,
    UNPROTECTED_TABLES,
    WORKSPACE_TABLES,
)
from backend.database.saas_models import (
    ActionItem,
    ExternalRecord,
    Meeting,
    PurgeState,
    ReviewItem,
    ShareAudience,
    ShareLink,
    TranscriptRevision,
    TranscriptSource,
    User,
    Workspace,
)
from backend.database.tenant_context import bind_capability_context, bind_tenant_context
from backend.services.conversation_purge_service import _remove_content
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user, register_user
from backend.tests.test_ingestion_ledger import _patch_media
from backend.tests.test_ingestion_ledger import _tenant as _ledger_tenant
from backend.tests.test_retention_purge import _processed, _purge
from backend.tests.test_scorecard_results_migration import _delete_conversation

BASE_REVISION = "d3d1c7aa3cd8"
PROPOSALS = "action_proposals"
REQUESTS = "action_delivery_requests"
TABLES = (PROPOSALS, REQUESTS)
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
HASH = "0123456789abcdef" * 4
DIGEST = "fedcba9876543210" * 4
PAYLOAD = {"subject": "Call the customer back", "priority": "high"}
BAD_HEX = (
    "a" * 63,
    "A" * 64,
    "0123456789ABCDEF" * 4,
    "g" * 64,
    " " + "a" * 63,
    "",
)
GOOD_HEX = (HASH, "f" * 64, "0" * 64)
BAD_SLUGS = ("Bad Slug", "Zendesk", "9zendesk", "zen desk", "_zendesk", "zendesk!", "")
GOOD_SLUGS = ("zendesk", "z", "my-crm.v2_x", "a" * 60)
BAD_CRITERIA = ("9x", "G", "Greeting", "a-b", "a", "")
GOOD_CRITERIA = ("greeting", "c0", "a" * 64)
PROPOSAL_USER_COLUMNS = ("created_by_user_id", "decided_by_user_id", "revoked_by_user_id")
REQUEST_USER_COLUMNS = ("approved_by_user_id",)
PROPOSAL_CHECKS = (
    "ck_action_proposals_action_type",
    "ck_action_proposals_source_type",
    "ck_action_proposals_status",
    "ck_action_proposals_revision",
    "ck_action_proposals_payload_version",
    "ck_action_proposals_payload_hash",
    "ck_action_proposals_source_digest",
    "ck_action_proposals_criterion_key",
    "ck_action_proposals_target_system",
    "ck_action_proposals_source_shape",
    "ck_action_proposals_type_source",
    "ck_action_proposals_target_shape",
    "ck_action_proposals_reject_reason",
    "ck_action_proposals_revoke_reason",
    "ck_action_proposals_pending_fields",
    "ck_action_proposals_approved_fields",
    "ck_action_proposals_rejected_fields",
    "ck_action_proposals_revoked_fields",
)
REQUEST_CHECKS = (
    "ck_action_delivery_requests_action_type",
    "ck_action_delivery_requests_target_shape",
    "ck_action_delivery_requests_type_source",
)
REQUEST_CHECK_PATTERNS = {
    "payload_hash": r"\bck_action_delivery_requests_\w*payload_hash\w*",
    "source_digest": r"\bck_action_delivery_requests_\w*source_digest\w*",
    "idempotency_key": r"\bck_action_delivery_requests_\w*idempotency_key\w*",
    "payload_version": r"\bck_action_delivery_requests_\w*payload_version\w*",
    "source_type": r"\bck_action_delivery_requests_\w*source_type\w*",
    "criterion": r"\bck_action_delivery_requests_\w*criterion\w*",
}
UNIQUE_COLUMNS = {
    "uq_action_proposals_open_item": (PROPOSALS, ("action_item_id", "action_type")),
    "uq_action_proposals_open_finding": (PROPOSALS, ("review_item_id", "criterion_id")),
    "uq_action_delivery_requests_proposal": (REQUESTS, ("proposal_id",)),
    "uq_action_delivery_requests_idempotency_key": (REQUESTS, ("idempotency_key",)),
}
PARTIAL_INDEXES = {
    "uq_action_proposals_open_item": ("action_item_id", "action_type"),
    "uq_action_proposals_open_finding": ("review_item_id", "criterion_id"),
}
UNIQUE_KEYS = {
    PROPOSALS: {"uq_action_proposals_scope": ("id", "workspace_id", "meeting_id")},
    REQUESTS: {
        "uq_action_delivery_requests_proposal": ("proposal_id",),
        "uq_action_delivery_requests_idempotency_key": ("idempotency_key",),
        "uq_action_delivery_requests_scope": ("id", "workspace_id", "meeting_id"),
    },
}
PLAIN_INDEXES = {
    PROPOSALS: {
        "ix_action_proposals_workspace_id": ("workspace_id",),
        "ix_action_proposals_queue": ("workspace_id", "status", "created_at", "id"),
        "ix_action_proposals_workspace_meeting": ("workspace_id", "meeting_id"),
    },
    REQUESTS: {
        "ix_action_delivery_requests_workspace_id": ("workspace_id",),
        "ix_action_delivery_requests_workspace_meeting": ("workspace_id", "meeting_id"),
        "ix_action_delivery_requests_approved": ("workspace_id", "approved_at", "id"),
    },
}
FK_LEADING_INDEXES = {
    PROPOSALS: (
        "action_item_id",
        "review_item_id",
        "source_transcript_revision_id",
        "target_external_record_id",
    ),
    REQUESTS: ("target_external_record_id",),
}


def _on_postgres() -> bool:
    return is_postgres_url(configured_database_url())


def _requires_postgres() -> None:
    if not _on_postgres():
        pytest.skip("needs PostgreSQL")


def _models():
    from backend.database.saas_models import ActionDeliveryRequest, ActionProposal

    return ActionProposal, ActionDeliveryRequest


def _scalar(sql: str, **params):
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


def _ids(table: str, **where: str) -> set[str]:
    clauses = " AND ".join(f"{column} = :{column}" for column in where) or "1 = 1"
    with inspection_session_factory()() as db:
        return set(
            db.execute(text(f"SELECT id FROM {table} WHERE {clauses}"), where).scalars()  # noqa: S608
        )


def _tenant(client, email_outbox, label: str) -> dict:
    credentials = register_user(client, email_outbox, email=f"action-oracle-{label}@example.com")
    session = login_user(client, credentials)
    workspace = session["workspaces"][0]
    return {
        "user_id": session["user"]["id"],
        "workspace_id": workspace["id"],
        "organization_id": workspace["organization_id"],
    }


def _review_values(tenant: dict, meeting_id: str) -> dict[str, object]:
    return {
        "workspace_id": tenant["workspace_id"],
        "meeting_id": meeting_id,
        "source": "manual",
        "source_details": {},
        "status": "completed",
        "started_at": NOW,
        "completed_at": NOW,
        "due_at": NOW + timedelta(days=2),
        "created_by_user_id": tenant["user_id"],
        "assignee_user_id": tenant["user_id"],
        "revision": 1,
    }


def _conversation(tenant: dict, key: str, *, meeting_id: str | None = None) -> dict:
    with inspection_session_factory()() as db:
        if meeting_id is None:
            meeting = Meeting(
                workspace_id=tenant["workspace_id"],
                created_by_user_id=tenant["user_id"],
                title="Proposed call",
            )
            db.add(meeting)
            db.flush()
            meeting_id = meeting.id
            revision = TranscriptRevision(
                workspace_id=tenant["workspace_id"],
                meeting_id=meeting_id,
                revision_number=1,
                source=TranscriptSource.ASR,
                reason="processing",
                language="fa",
                segment_count=0,
                is_current=True,
            )
            db.add(revision)
            db.flush()
            revision_id = revision.id
        else:
            revision_id = db.scalar(
                select(TranscriptRevision.id).where(
                    TranscriptRevision.meeting_id == meeting_id,
                    TranscriptRevision.is_current.is_(True),
                )
            )
            assert revision_id is not None
        item = ActionItem(
            workspace_id=tenant["workspace_id"],
            meeting_id=meeting_id,
            description="Call the customer back",
        )
        review = ReviewItem(**_review_values(tenant, meeting_id))
        record = ExternalRecord(
            organization_id=tenant["organization_id"],
            workspace_id=tenant["workspace_id"],
            source_system="hubspot",
            record_type="customer",
            external_id=f"C-{key}",
        )
        db.add_all([item, review, record])
        db.commit()
        return {
            **tenant,
            "key": key,
            "meeting_id": meeting_id,
            "revision_id": revision_id,
            "action_item_id": item.id,
            "review_item_id": review.id,
            "record_id": record.id,
        }


def _fresh_item(conv: dict) -> dict:
    with inspection_session_factory()() as db:
        item = ActionItem(
            workspace_id=conv["workspace_id"],
            meeting_id=conv["meeting_id"],
            description="Send the quote",
        )
        db.add(item)
        db.commit()
        return conv | {"action_item_id": item.id}


def _fresh_review(conv: dict) -> dict:
    with inspection_session_factory()() as db:
        review = ReviewItem(**_review_values(conv, conv["meeting_id"]))
        db.add(review)
        db.commit()
        return conv | {"review_item_id": review.id}


def _fresh_record(conv: dict, suffix: str) -> str:
    with inspection_session_factory()() as db:
        record = ExternalRecord(
            organization_id=conv["organization_id"],
            workspace_id=conv["workspace_id"],
            source_system="hubspot",
            record_type="customer",
            external_id=f"C-{conv['key']}-{suffix}",
        )
        db.add(record)
        db.commit()
        return record.id


def _base(conv: dict) -> dict[str, object]:
    return {
        "workspace_id": conv["workspace_id"],
        "meeting_id": conv["meeting_id"],
        "source_transcript_revision_id": conv["revision_id"],
        "source_digest": DIGEST,
        "payload_json": PAYLOAD,
        "payload_hash": HASH,
        "payload_version": 1,
        "status": "pending",
        "revision": 1,
        "created_by_user_id": conv["user_id"],
    }


def _ticket(conv: dict, **extra: object) -> dict[str, object]:
    return {
        **_base(conv),
        "action_type": "ticket_create",
        "source_type": "action_item",
        "action_item_id": conv["action_item_id"],
        "review_item_id": None,
        "criterion_id": None,
        "target_system": "zendesk",
        "target_external_record_id": None,
        **extra,
    }


def _crm(conv: dict, **extra: object) -> dict[str, object]:
    return {
        **_base(conv),
        "action_type": "crm_update",
        "source_type": "action_item",
        "action_item_id": conv["action_item_id"],
        "review_item_id": None,
        "criterion_id": None,
        "target_system": "hubspot",
        "target_external_record_id": conv["record_id"],
        **extra,
    }


def _coaching(conv: dict, **extra: object) -> dict[str, object]:
    return {
        **_base(conv),
        "action_type": "coaching_task",
        "source_type": "review_finding",
        "action_item_id": None,
        "review_item_id": conv["review_item_id"],
        "criterion_id": "greeting",
        "target_system": None,
        "target_external_record_id": None,
        **extra,
    }


def _approved(values: dict, **extra: object) -> dict[str, object]:
    return {
        **values,
        "status": "approved",
        "decided_at": NOW,
        "decided_by_user_id": values["created_by_user_id"],
        **extra,
    }


def _rejected(values: dict, **extra: object) -> dict[str, object]:
    return {
        **values,
        "status": "rejected",
        "decided_at": NOW,
        "decided_by_user_id": values["created_by_user_id"],
        "reject_reason": "duplicate",
        **extra,
    }


def _revoked(values: dict, **extra: object) -> dict[str, object]:
    return {
        **values,
        "status": "revoked",
        "decided_at": NOW,
        "decided_by_user_id": values["created_by_user_id"],
        "revoked_at": NOW + timedelta(minutes=5),
        "revoked_by_user_id": values["created_by_user_id"],
        "revoke_reason": "approved_in_error",
        **extra,
    }


def _key(workspace_id: str, proposal_id: str, payload_hash: str = HASH) -> str:
    return hashlib.sha256(f"{workspace_id}|{proposal_id}|{payload_hash}".encode()).hexdigest()


def _request(conv: dict, proposal_id: str, **extra: object) -> dict[str, object]:
    return {
        "workspace_id": conv["workspace_id"],
        "meeting_id": conv["meeting_id"],
        "proposal_id": proposal_id,
        "action_type": "ticket_create",
        "target_system": "zendesk",
        "target_external_record_id": None,
        "payload_json": PAYLOAD,
        "payload_hash": HASH,
        "payload_version": 1,
        "source_type": "action_item",
        "source_id": conv["action_item_id"],
        "source_criterion_id": None,
        "source_transcript_revision_id": conv["revision_id"],
        "source_digest": DIGEST,
        "idempotency_key": _key(conv["workspace_id"], proposal_id),
        "approved_by_user_id": conv["user_id"],
        "approved_at": NOW,
        **extra,
    }


def _crm_request(conv: dict, proposal_id: str, **extra: object) -> dict[str, object]:
    return _request(
        conv,
        proposal_id,
        **{
            "action_type": "crm_update",
            "target_system": "hubspot",
            "target_external_record_id": conv["record_id"],
            **extra,
        },
    )


def _coaching_request(conv: dict, proposal_id: str, **extra: object) -> dict[str, object]:
    return _request(
        conv,
        proposal_id,
        **{
            "action_type": "coaching_task",
            "target_system": None,
            "target_external_record_id": None,
            "source_type": "review_finding",
            "source_id": conv["review_item_id"],
            "source_criterion_id": "greeting",
            **extra,
        },
    )


def _add(model, values: dict[str, object]) -> str:
    with inspection_session_factory()() as db:
        row = model(**values)
        db.add(row)
        db.commit()
        return row.id


def _proposal(values: dict[str, object]) -> str:
    return _add(_models()[0], values)


def _delivery(values: dict[str, object]) -> str:
    return _add(_models()[1], values)


def _approved_ticket(conv: dict) -> str:
    return _proposal(_approved(_ticket(_fresh_item(conv))))


def _pattern(constraint: str) -> str:
    if constraint.startswith(r"\b"):
        return constraint
    if constraint.startswith("fk_") and not _on_postgres():
        return "FOREIGN KEY constraint failed"
    if constraint in UNIQUE_COLUMNS and not _on_postgres():
        table, columns = UNIQUE_COLUMNS[constraint]
        return re.escape(", ".join(f"{table}.{column}" for column in columns))
    return rf"\b{constraint}\b"


def _refused(model, values: dict[str, object], constraint: str) -> None:
    with inspection_session_factory()() as db:
        db.add(model(**values))
        with pytest.raises(IntegrityError, match=_pattern(constraint)):
            db.flush()
        db.rollback()


def _assert_action_type_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "action_type")
    for value in ("email", "Ticket_create", "ticket_create ", ""):
        _refused(proposal, _ticket(conv, action_type=value), "ck_action_proposals_action_type")
    _proposal(_ticket(_fresh_item(conv), action_type="ticket_create"))
    _proposal(_crm(_fresh_item(conv)))
    _proposal(_coaching(_fresh_review(conv)))


def _assert_source_type_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "source_type")
    for value in ("summary", "Action_item", "action_item ", ""):
        _refused(proposal, _ticket(conv, source_type=value), "ck_action_proposals_source_type")
    _proposal(_ticket(_fresh_item(conv), source_type="action_item"))
    _proposal(_coaching(_fresh_review(conv), source_type="review_finding"))


def _assert_status_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "status")
    for value in ("done", "Pending", "pending ", ""):
        _refused(proposal, _ticket(conv, status=value), "ck_action_proposals_status")
    _proposal(_ticket(_fresh_item(conv), status="pending"))
    _proposal(_approved(_ticket(_fresh_item(conv))))
    _proposal(_rejected(_ticket(_fresh_item(conv))))
    _proposal(_revoked(_ticket(_fresh_item(conv))))


def _assert_revision_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "revision")
    for value in (0, -1):
        _refused(proposal, _ticket(conv, revision=value), "ck_action_proposals_revision")
    _proposal(_ticket(_fresh_item(conv), revision=1))
    _proposal(_ticket(_fresh_item(conv), revision=2))


def _assert_payload_version_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "payload_version")
    for value in (0, -1):
        _refused(
            proposal, _ticket(conv, payload_version=value), "ck_action_proposals_payload_version"
        )
    _proposal(_ticket(_fresh_item(conv), payload_version=1))
    _proposal(_ticket(_fresh_item(conv), payload_version=3))


def _assert_payload_hash_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "payload_hash")
    for value in BAD_HEX:
        _refused(proposal, _ticket(conv, payload_hash=value), "ck_action_proposals_payload_hash")
    for value in GOOD_HEX:
        _proposal(_ticket(_fresh_item(conv), payload_hash=value))


def _assert_source_digest_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "source_digest")
    for value in BAD_HEX:
        _refused(proposal, _ticket(conv, source_digest=value), "ck_action_proposals_source_digest")
    for value in GOOD_HEX:
        _proposal(_ticket(_fresh_item(conv), source_digest=value))


def _assert_source_shape_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "source_shape")
    for values in (
        _ticket(conv, criterion_id="greeting"),
        _ticket(conv, review_item_id=conv["review_item_id"]),
        _coaching(conv, action_item_id=conv["action_item_id"]),
        _coaching(conv, criterion_id=None),
    ):
        _refused(proposal, values, "ck_action_proposals_source_shape")
    _proposal(_ticket(_fresh_item(conv), criterion_id=None, review_item_id=None))
    _proposal(_coaching(_fresh_review(conv), criterion_id="greeting"))
    _proposal(_ticket(conv, action_item_id=None))
    _proposal(_coaching(conv, review_item_id=None))


def _assert_type_source_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "type_source")
    finding = {
        "source_type": "review_finding",
        "action_item_id": None,
        "review_item_id": conv["review_item_id"],
        "criterion_id": "greeting",
    }
    item = {
        "source_type": "action_item",
        "action_item_id": conv["action_item_id"],
        "review_item_id": None,
        "criterion_id": None,
    }
    for values in (_crm(conv, **finding), _ticket(conv, **finding), _coaching(conv, **item)):
        _refused(proposal, values, "ck_action_proposals_type_source")
    _proposal(_crm(_fresh_item(conv)))
    _proposal(_ticket(_fresh_item(conv)))
    _proposal(_coaching(_fresh_review(conv)))


def _assert_target_shape_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "target_shape")
    for values in (
        _crm(conv, target_external_record_id=None),
        _crm(conv, target_system=None),
        _ticket(conv, target_external_record_id=conv["record_id"]),
        _ticket(conv, target_system=None),
        _coaching(conv, target_system="zendesk"),
        _coaching(conv, target_external_record_id=conv["record_id"]),
    ):
        _refused(proposal, values, "ck_action_proposals_target_shape")
    _proposal(_crm(_fresh_item(conv)))
    _proposal(_ticket(_fresh_item(conv)))
    _proposal(_coaching(_fresh_review(conv)))


def _assert_target_system_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "target_system")
    for value in BAD_SLUGS:
        _refused(proposal, _ticket(conv, target_system=value), "ck_action_proposals_target_system")
    _refused(proposal, _crm(conv, target_system="Bad Slug"), "ck_action_proposals_target_system")
    for value in GOOD_SLUGS:
        _proposal(_ticket(_fresh_item(conv), target_system=value))
    _proposal(_crm(_fresh_item(conv), target_system="hubspot"))


def _assert_criterion_key_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "criterion_key")
    for value in BAD_CRITERIA:
        _refused(proposal, _coaching(conv, criterion_id=value), "ck_action_proposals_criterion_key")
    for value in GOOD_CRITERIA:
        _proposal(_coaching(conv, criterion_id=value))


def _assert_reject_reason_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "reject_reason")
    for value in ("spam", "Duplicate", "duplicate ", ""):
        _refused(
            proposal,
            _rejected(_ticket(conv), reject_reason=value),
            "ck_action_proposals_reject_reason",
        )
    for value in ("not_needed", "incorrect", "duplicate", "other"):
        _proposal(_rejected(_ticket(conv), reject_reason=value))


def _assert_revoke_reason_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "revoke_reason")
    for value in ("x", "Other", "other ", ""):
        _refused(
            proposal,
            _revoked(_ticket(conv), revoke_reason=value),
            "ck_action_proposals_revoke_reason",
        )
    for value in ("approved_in_error", "no_longer_needed", "other"):
        _proposal(_revoked(_ticket(conv), revoke_reason=value))


def _assert_pending_fields_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "pending_fields")
    for column, value in (
        ("decided_at", NOW),
        ("reject_reason", "other"),
        ("revoked_at", NOW),
        ("revoke_reason", "other"),
    ):
        _refused(proposal, _ticket(conv, **{column: value}), "ck_action_proposals_pending_fields")
    _proposal(_ticket(_fresh_item(conv)))
    _proposal(_ticket(_fresh_item(conv), decided_by_user_id=conv["user_id"]))
    _proposal(_ticket(_fresh_item(conv), created_by_user_id=None))


def _assert_approved_fields_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "approved_fields")
    for column, value in (
        ("decided_at", None),
        ("reject_reason", "other"),
        ("revoked_at", NOW),
        ("revoke_reason", "other"),
    ):
        _refused(
            proposal,
            _approved(_ticket(conv), **{column: value}),
            "ck_action_proposals_approved_fields",
        )
    _proposal(_approved(_ticket(_fresh_item(conv))))
    _proposal(_approved(_ticket(_fresh_item(conv)), decided_by_user_id=None))


def _assert_rejected_fields_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "rejected_fields")
    for column, value in (
        ("reject_reason", None),
        ("decided_at", None),
        ("revoked_at", NOW),
        ("revoke_reason", "other"),
    ):
        _refused(
            proposal,
            _rejected(_ticket(conv), **{column: value}),
            "ck_action_proposals_rejected_fields",
        )
    _proposal(_rejected(_ticket(conv)))
    _proposal(_rejected(_ticket(conv), decided_by_user_id=None))


def _assert_revoked_fields_rules(tenant: dict) -> None:
    proposal, _ = _models()
    conv = _conversation(tenant, "revoked_fields")
    for column, value in (
        ("revoked_at", None),
        ("revoke_reason", None),
        ("decided_at", None),
        ("reject_reason", "other"),
    ):
        _refused(
            proposal,
            _revoked(_ticket(conv), **{column: value}),
            "ck_action_proposals_revoked_fields",
        )
    _proposal(_revoked(_ticket(conv)))
    _proposal(_revoked(_ticket(conv), revoked_by_user_id=None, decided_by_user_id=None))


def _assert_request_action_type_rules(tenant: dict) -> None:
    _, request = _models()
    conv = _conversation(tenant, "request_action_type")
    proposal_id = _approved_ticket(conv)
    for value in ("email", "Ticket_create", ""):
        _refused(
            request,
            _request(conv, proposal_id, action_type=value),
            "ck_action_delivery_requests_action_type",
        )
    _delivery(_request(conv, proposal_id, action_type="ticket_create"))
    _delivery(_crm_request(conv, _approved_ticket(conv)))
    _delivery(_coaching_request(conv, _approved_ticket(conv)))


def _assert_request_source_type_rules(tenant: dict) -> None:
    _, request = _models()
    conv = _conversation(tenant, "request_source_type")
    proposal_id = _approved_ticket(conv)
    for value in ("summary", "Action_item", ""):
        _refused(
            request,
            _request(conv, proposal_id, source_type=value),
            REQUEST_CHECK_PATTERNS["source_type"],
        )
    _delivery(_request(conv, proposal_id, source_type="action_item"))
    _delivery(_coaching_request(conv, _approved_ticket(conv)))


def _assert_request_payload_version_rules(tenant: dict) -> None:
    _, request = _models()
    conv = _conversation(tenant, "request_payload_version")
    proposal_id = _approved_ticket(conv)
    for value in (0, -1):
        _refused(
            request,
            _request(conv, proposal_id, payload_version=value),
            REQUEST_CHECK_PATTERNS["payload_version"],
        )
    _delivery(_request(conv, proposal_id, payload_version=1))
    _delivery(_request(conv, _approved_ticket(conv), payload_version=2))


def _request_hex_rules(tenant: dict, column: str) -> None:
    _, request = _models()
    conv = _conversation(tenant, f"request_{column}")
    proposal_id = _approved_ticket(conv)
    for value in BAD_HEX:
        _refused(
            request, _request(conv, proposal_id, **{column: value}), REQUEST_CHECK_PATTERNS[column]
        )
    _delivery(_request(conv, proposal_id))
    for value in GOOD_HEX:
        _delivery(_request(conv, _approved_ticket(conv), **{column: value}))


def _assert_request_payload_hash_rules(tenant: dict) -> None:
    _request_hex_rules(tenant, "payload_hash")


def _assert_request_source_digest_rules(tenant: dict) -> None:
    _request_hex_rules(tenant, "source_digest")


def _assert_request_idempotency_key_rules(tenant: dict) -> None:
    _request_hex_rules(tenant, "idempotency_key")


def _assert_request_type_source_rules(tenant: dict) -> None:
    _, request = _models()
    conv = _conversation(tenant, "request_type_source")
    proposal_id = _approved_ticket(conv)
    finding = {
        "source_type": "review_finding",
        "source_id": conv["review_item_id"],
        "source_criterion_id": "greeting",
    }
    item = {"source_type": "action_item", "source_id": conv["action_item_id"]}
    for values in (
        _crm_request(conv, proposal_id, **finding),
        _request(conv, proposal_id, **finding),
        _coaching_request(conv, proposal_id, source_criterion_id=None, **item),
    ):
        _refused(request, values, "ck_action_delivery_requests_type_source")
    _delivery(_crm_request(conv, proposal_id))
    _delivery(_request(conv, _approved_ticket(conv)))
    _delivery(_coaching_request(conv, _approved_ticket(conv)))


def _assert_request_target_shape_rules(tenant: dict) -> None:
    _, request = _models()
    conv = _conversation(tenant, "request_target_shape")
    proposal_id = _approved_ticket(conv)
    for values in (
        _crm_request(conv, proposal_id, target_external_record_id=None),
        _crm_request(conv, proposal_id, target_system=None),
        _request(conv, proposal_id, target_external_record_id=conv["record_id"]),
        _request(conv, proposal_id, target_system=None),
        _coaching_request(conv, proposal_id, target_system="zendesk"),
        _coaching_request(conv, proposal_id, target_external_record_id=conv["record_id"]),
    ):
        _refused(request, values, "ck_action_delivery_requests_target_shape")
    _delivery(_crm_request(conv, proposal_id))
    _delivery(_request(conv, _approved_ticket(conv)))
    _delivery(_coaching_request(conv, _approved_ticket(conv)))


def _assert_request_criterion_key_rules(tenant: dict) -> None:
    _, request = _models()
    conv = _conversation(tenant, "request_criterion_key")
    proposal_id = _approved_ticket(conv)
    for value in BAD_CRITERIA:
        _refused(
            request,
            _coaching_request(conv, proposal_id, source_criterion_id=value),
            REQUEST_CHECK_PATTERNS["criterion"],
        )
    _delivery(_coaching_request(conv, proposal_id, source_criterion_id="greeting"))
    _delivery(_request(conv, _approved_ticket(conv), source_criterion_id=None))


RULES = (
    _assert_action_type_rules,
    _assert_source_type_rules,
    _assert_status_rules,
    _assert_revision_rules,
    _assert_payload_version_rules,
    _assert_payload_hash_rules,
    _assert_source_digest_rules,
    _assert_source_shape_rules,
    _assert_type_source_rules,
    _assert_target_shape_rules,
    _assert_target_system_rules,
    _assert_criterion_key_rules,
    _assert_reject_reason_rules,
    _assert_revoke_reason_rules,
    _assert_pending_fields_rules,
    _assert_approved_fields_rules,
    _assert_rejected_fields_rules,
    _assert_revoked_fields_rules,
    _assert_request_action_type_rules,
    _assert_request_source_type_rules,
    _assert_request_payload_version_rules,
    _assert_request_payload_hash_rules,
    _assert_request_source_digest_rules,
    _assert_request_idempotency_key_rules,
    _assert_request_type_source_rules,
    _assert_request_target_shape_rules,
    _assert_request_criterion_key_rules,
)


@pytest.mark.parametrize("rules", RULES, ids=lambda rules: rules.__name__.removeprefix("_assert_"))
def test_the_database_refuses_each_deny_case_by_name_and_takes_its_allow_control(
    client, email_outbox, rules
):
    rules(_tenant(client, email_outbox, "rules"))


def test_at_most_one_open_proposal_per_action_item_and_action_type(client, email_outbox):
    proposal, _ = _models()
    conv = _conversation(_tenant(client, email_outbox, "open-item"), "open_item")
    pending = _fresh_item(conv)
    _proposal(_ticket(pending))
    _refused(proposal, _ticket(pending), "uq_action_proposals_open_item")
    _refused(proposal, _approved(_ticket(pending)), "uq_action_proposals_open_item")
    _proposal(_crm(pending))
    _refused(proposal, _crm(pending), "uq_action_proposals_open_item")
    approved = _fresh_item(conv)
    _proposal(_approved(_ticket(approved)))
    _refused(proposal, _ticket(approved), "uq_action_proposals_open_item")
    rejected = _fresh_item(conv)
    _proposal(_rejected(_ticket(rejected)))
    _proposal(_rejected(_ticket(rejected)))
    _proposal(_ticket(rejected))
    revoked = _fresh_item(conv)
    _proposal(_revoked(_ticket(revoked)))
    _proposal(_approved(_ticket(revoked)))
    removed = _ticket(conv, action_item_id=None)
    _proposal(removed)
    _proposal(removed)


def test_at_most_one_open_proposal_per_finding(client, email_outbox):
    proposal, _ = _models()
    conv = _conversation(_tenant(client, email_outbox, "open-finding"), "open_finding")
    pending = _fresh_review(conv)
    _proposal(_coaching(pending))
    _refused(proposal, _coaching(pending), "uq_action_proposals_open_finding")
    _refused(proposal, _approved(_coaching(pending)), "uq_action_proposals_open_finding")
    _proposal(_coaching(pending, criterion_id="closing"))
    approved = _fresh_review(conv)
    _proposal(_approved(_coaching(approved)))
    _refused(proposal, _coaching(approved), "uq_action_proposals_open_finding")
    rejected = _fresh_review(conv)
    _proposal(_rejected(_coaching(rejected)))
    _proposal(_coaching(rejected))
    revoked = _fresh_review(conv)
    _proposal(_revoked(_coaching(revoked)))
    _proposal(_coaching(revoked))
    removed = _coaching(conv, review_item_id=None)
    _proposal(removed)
    _proposal(removed)


def test_one_delivery_request_per_proposal_and_per_idempotency_key(client, email_outbox):
    _, request = _models()
    conv = _conversation(_tenant(client, email_outbox, "request-unique"), "request_unique")
    first = _approved_ticket(conv)
    first_key = _key(conv["workspace_id"], first)
    _delivery(_request(conv, first))
    _refused(
        request,
        _request(conv, first, idempotency_key="b" * 64),
        "uq_action_delivery_requests_proposal",
    )
    second = _approved_ticket(conv)
    _delivery(_request(conv, second, idempotency_key="b" * 64))
    third = _approved_ticket(conv)
    _refused(
        request,
        _request(conv, third, idempotency_key=first_key),
        "uq_action_delivery_requests_idempotency_key",
    )
    _delivery(_request(conv, third))
    assert len(_ids(REQUESTS, meeting_id=conv["meeting_id"])) == 3


def test_a_delivery_request_stays_in_its_proposals_workspace_and_meeting(client, email_outbox):
    _, request = _models()
    tenant = _tenant(client, email_outbox, "scope-a")
    other = _tenant(client, email_outbox, "scope-b")
    conv = _conversation(tenant, "scope_a")
    sibling = _conversation(tenant, "scope_sibling")
    foreign = _conversation(other, "scope_b")
    proposal_id = _approved_ticket(conv)
    for values in (
        _request(conv, proposal_id, meeting_id=sibling["meeting_id"]),
        _request(conv, proposal_id, workspace_id=other["workspace_id"]),
        _request(
            conv,
            proposal_id,
            workspace_id=other["workspace_id"],
            meeting_id=foreign["meeting_id"],
        ),
    ):
        _refused(request, values, "fk_action_delivery_requests_proposal")
    _delivery(_request(conv, proposal_id))
    assert _ids(REQUESTS, meeting_id=conv["meeting_id"]) != set()


def _flush_error(db) -> Exception | None:
    try:
        db.flush()
    except Exception as exc:
        return exc
    return None


def test_the_orm_refuses_any_update_of_a_delivery_request(client, email_outbox):
    _, request = _models()
    from backend.database.saas_models import ActionDeliveryRequestImmutableError

    assert issubclass(ActionDeliveryRequestImmutableError, RuntimeError)
    conv = _conversation(_tenant(client, email_outbox, "immutable"), "immutable")
    request_id = _delivery(_request(conv, _approved_ticket(conv)))
    statements: list[str] = []

    def record(_connection, _cursor, statement, *_args) -> None:
        statements.append(statement)

    for column, value in (
        ("payload_hash", "b" * 64),
        ("payload_version", 2),
        ("idempotency_key", "c" * 64),
        ("target_system", "freshdesk"),
        ("approved_by_user_id", None),
    ):
        statements.clear()
        with inspection_session_factory()() as db:
            row = db.get(request, request_id)
            setattr(row, column, value)
            engine = db.get_bind()
            event.listen(engine, "before_cursor_execute", record)
            try:
                error = _flush_error(db)
            finally:
                event.remove(engine, "before_cursor_execute", record)
            db.rollback()
        assert isinstance(error, ActionDeliveryRequestImmutableError), (column, error)
        assert not [s for s in statements if s.lstrip().upper().startswith("UPDATE")], column
    with inspection_session_factory()() as db:
        row = db.get(request, request_id)
        assert (row.payload_hash, row.payload_version, row.target_system) == (HASH, 1, "zendesk")
        assert row.approved_by_user_id == conv["user_id"]
        db.delete(row)
        db.commit()
    assert _ids(REQUESTS, meeting_id=conv["meeting_id"]) == set()


def test_a_new_proposal_and_request_take_their_defaults(client, email_outbox):
    proposal, request = _models()
    conv = _conversation(_tenant(client, email_outbox, "defaults"), "defaults")
    values = _ticket(conv)
    for column in ("status", "revision", "payload_version"):
        values.pop(column)
    proposal_id = _proposal(values)
    request_values = _request(conv, proposal_id)
    request_id = _delivery(request_values)
    with inspection_session_factory()() as db:
        stored = db.get(proposal, proposal_id)
        assert len(stored.id) == 36
        assert (stored.status, stored.revision, stored.payload_version) == ("pending", 1, 1)
        assert stored.created_at is not None and stored.updated_at is not None
        assert stored.payload_json == PAYLOAD
        delivered = db.get(request, request_id)
        assert len(delivered.id) == 36
        assert delivered.created_at is not None
        assert delivered.payload_json == PAYLOAD


def test_deleting_a_meeting_or_a_workspace_removes_both_tables_rows(client, email_outbox):
    first = _tenant(client, email_outbox, "cascade-a")
    second = _tenant(client, email_outbox, "cascade-b")
    by_meeting = _conversation(second, "cascade_meeting")
    kept = _conversation(second, "cascade_kept")
    gone = _conversation(first, "cascade_workspace")
    rows = {}
    for label, conv in (("meeting", by_meeting), ("kept", kept), ("workspace", gone)):
        crm_id = _proposal(_approved(_crm(conv)))
        coaching_id = _proposal(_coaching(conv))
        rows[label] = (crm_id, coaching_id, _delivery(_crm_request(conv, crm_id)))
    with inspection_session_factory()() as db:
        db.execute(delete(Meeting).where(Meeting.id == by_meeting["meeting_id"]))
        db.commit()
    assert _ids(PROPOSALS, meeting_id=by_meeting["meeting_id"]) == set()
    assert _ids(REQUESTS, meeting_id=by_meeting["meeting_id"]) == set()
    with inspection_session_factory()() as db:
        db.execute(delete(Workspace).where(Workspace.id == first["workspace_id"]))
        db.commit()
    assert _ids(PROPOSALS, workspace_id=first["workspace_id"]) == set()
    assert _ids(REQUESTS, workspace_id=first["workspace_id"]) == set()
    assert _ids(PROPOSALS) == {rows["kept"][0], rows["kept"][1]}
    assert _ids(REQUESTS) == {rows["kept"][2]}


def test_deleting_a_proposal_removes_its_request_only(client, email_outbox):
    proposal, _ = _models()
    conv = _conversation(_tenant(client, email_outbox, "proposal-delete"), "proposal_delete")
    doomed = _approved_ticket(conv)
    doomed_request = _delivery(_request(conv, doomed))
    kept = _approved_ticket(conv)
    kept_request = _delivery(_request(conv, kept))
    with inspection_session_factory()() as db:
        db.execute(delete(proposal).where(proposal.id == doomed))
        db.commit()
    assert doomed_request not in _ids(REQUESTS)
    assert _ids(REQUESTS, meeting_id=conv["meeting_id"]) == {kept_request}
    assert _ids(PROPOSALS, meeting_id=conv["meeting_id"]) == {kept}


def test_deleting_the_source_keeps_the_proposal_with_the_source_id_cleared(client, email_outbox):
    proposal, request = _models()
    conv = _conversation(_tenant(client, email_outbox, "source-delete"), "source_delete")
    from_item = _proposal(_approved(_ticket(conv)))
    item_request = _delivery(_request(conv, from_item))
    from_finding = _proposal(_approved(_coaching(conv)))
    finding_request = _delivery(_coaching_request(conv, from_finding))
    with inspection_session_factory()() as db:
        db.execute(delete(ActionItem).where(ActionItem.id == conv["action_item_id"]))
        db.execute(delete(ReviewItem).where(ReviewItem.id == conv["review_item_id"]))
        db.commit()
    with inspection_session_factory()() as db:
        item_row = db.get(proposal, from_item)
        assert item_row is not None
        assert item_row.action_item_id is None
        assert (item_row.status, item_row.source_type) == ("approved", "action_item")
        finding_row = db.get(proposal, from_finding)
        assert finding_row is not None
        assert finding_row.review_item_id is None
        assert finding_row.criterion_id == "greeting"
        assert db.get(request, item_request).source_id == conv["action_item_id"]
        delivered = db.get(request, finding_request)
        assert delivered.source_id == conv["review_item_id"]
        assert delivered.source_criterion_id == "greeting"


def test_deleting_a_transcript_revision_removes_its_proposals_and_their_requests(
    client, email_outbox
):
    tenant = _tenant(client, email_outbox, "revision-delete")
    conv = _conversation(tenant, "revision_delete")
    kept = _conversation(tenant, "revision_kept")
    doomed = _proposal(_approved(_ticket(conv)))
    _delivery(_request(conv, doomed))
    survivor = _proposal(_approved(_ticket(kept)))
    survivor_request = _delivery(_request(kept, survivor))
    with inspection_session_factory()() as db:
        db.execute(delete(TranscriptRevision).where(TranscriptRevision.id == conv["revision_id"]))
        db.commit()
    assert _ids(PROPOSALS, meeting_id=conv["meeting_id"]) == set()
    assert _ids(REQUESTS, meeting_id=conv["meeting_id"]) == set()
    assert _ids(PROPOSALS) == {survivor}
    assert _ids(REQUESTS) == {survivor_request}


def test_deleting_a_user_clears_the_user_columns_and_keeps_the_rows(client, email_outbox):
    proposal, request = _models()
    tenant = _tenant(client, email_outbox, "user-delete")
    with inspection_session_factory()() as db:
        user = User(email="action-oracle-removed@example.com", display_name="Removed")
        db.add(user)
        db.commit()
        user_id = user.id
    conv = _conversation(tenant, "user_delete")
    values = _revoked(_ticket(conv))
    values.update({column: user_id for column in PROPOSAL_USER_COLUMNS})
    proposal_id = _proposal(values)
    request_id = _delivery(_request(conv, proposal_id, approved_by_user_id=user_id))
    with inspection_session_factory()() as db:
        db.execute(delete(User).where(User.id == user_id))
        db.commit()
    with inspection_session_factory()() as db:
        row = db.get(proposal, proposal_id)
        for column in PROPOSAL_USER_COLUMNS:
            assert getattr(row, column) is None, column
        assert (row.status, row.revoke_reason, row.revision) == ("revoked", "approved_in_error", 1)
        delivered = db.get(request, request_id)
        assert delivered.approved_by_user_id is None
        assert delivered.payload_hash == HASH


def _restricted(table: str) -> str:
    if _on_postgres():
        return rf'on table "{table}"'
    return "FOREIGN KEY constraint failed"


def _delete_record(record_id: str) -> None:
    with inspection_session_factory()() as db:
        db.execute(delete(ExternalRecord).where(ExternalRecord.id == record_id))
        db.commit()


def test_an_external_record_named_by_a_proposal_or_a_request_cannot_be_deleted(
    client, email_outbox
):
    proposal, _ = _models()
    conv = _conversation(_tenant(client, email_outbox, "restrict"), "restrict")
    named = _proposal(_crm(conv))
    with pytest.raises(IntegrityError, match=_restricted(PROPOSALS)):
        _delete_record(conv["record_id"])
    second_record = _fresh_record(conv, "request")
    approved = _proposal(_approved(_crm(_fresh_item(conv))))
    _delivery(_crm_request(conv, approved, target_external_record_id=second_record))
    with pytest.raises(IntegrityError, match=_restricted(REQUESTS)):
        _delete_record(second_record)
    unused = _fresh_record(conv, "unused")
    _delete_record(unused)
    with inspection_session_factory()() as db:
        db.execute(delete(proposal).where(proposal.id.in_([named, approved])))
        db.commit()
    _delete_record(conv["record_id"])
    _delete_record(second_record)
    assert _ids("external_records", workspace_id=conv["workspace_id"]) == set()


PROPOSAL_COLUMNS = {
    "id": (String, 36, False),
    "workspace_id": (String, 36, False),
    "meeting_id": (String, 36, False),
    "action_type": (String, 20, False),
    "source_type": (String, 20, False),
    "action_item_id": (String, 36, True),
    "review_item_id": (String, 36, True),
    "criterion_id": (String, 64, True),
    "source_transcript_revision_id": (String, 36, False),
    "source_digest": (String, 64, False),
    "target_system": (String, 60, True),
    "target_external_record_id": (String, 36, True),
    "payload_json": (JSON, None, False),
    "payload_hash": (String, 64, False),
    "payload_version": (Integer, None, False),
    "status": (String, 20, False),
    "revision": (Integer, None, False),
    "created_by_user_id": (String, 36, True),
    "decided_by_user_id": (String, 36, True),
    "revoked_by_user_id": (String, 36, True),
    "decided_at": (DateTime, None, True),
    "revoked_at": (DateTime, None, True),
    "reject_reason": (String, 30, True),
    "revoke_reason": (String, 30, True),
    "created_at": (DateTime, None, False),
    "updated_at": (DateTime, None, False),
}
REQUEST_COLUMNS = {
    "id": (String, 36, False),
    "workspace_id": (String, 36, False),
    "meeting_id": (String, 36, False),
    "proposal_id": (String, 36, False),
    "action_type": (String, 20, False),
    "target_system": (String, 60, True),
    "target_external_record_id": (String, 36, True),
    "payload_json": (JSON, None, False),
    "payload_hash": (String, 64, False),
    "payload_version": (Integer, None, False),
    "source_type": (String, 20, False),
    "source_id": (String, 36, None),
    "source_criterion_id": (String, 64, True),
    "source_transcript_revision_id": (String, 36, False),
    "source_digest": (String, 64, False),
    "idempotency_key": (String, 64, False),
    "approved_by_user_id": (String, 36, True),
    "approved_at": (DateTime, None, False),
    "created_at": (DateTime, None, False),
}
PROPOSAL_FOREIGN_KEYS = {
    (("workspace_id",), ("workspaces.id",), "CASCADE"),
    (("meeting_id",), ("meetings.id",), "CASCADE"),
    (("action_item_id",), ("action_items.id",), "SET NULL"),
    (("review_item_id",), ("review_items.id",), "SET NULL"),
    (("source_transcript_revision_id",), ("transcript_revisions.id",), "CASCADE"),
    (("target_external_record_id",), ("external_records.id",), "RESTRICT"),
    (("created_by_user_id",), ("users.id",), "SET NULL"),
    (("decided_by_user_id",), ("users.id",), "SET NULL"),
    (("revoked_by_user_id",), ("users.id",), "SET NULL"),
}
REQUEST_FOREIGN_KEYS = {
    (("workspace_id",), ("workspaces.id",), "CASCADE"),
    (("meeting_id",), ("meetings.id",), "CASCADE"),
    (
        ("proposal_id", "workspace_id", "meeting_id"),
        ("action_proposals.id", "action_proposals.workspace_id", "action_proposals.meeting_id"),
        "CASCADE",
    ),
    (("target_external_record_id",), ("external_records.id",), "RESTRICT"),
    (("approved_by_user_id",), ("users.id",), "SET NULL"),
}


def _assert_columns(table, expected: dict) -> None:
    assert {column.name for column in table.columns} == set(expected), table.name
    for name, (kind, length, nullable) in expected.items():
        column = table.c[name]
        assert isinstance(column.type, kind), (table.name, name, column.type)
        if length is not None:
            assert column.type.length == length, (table.name, name)
        if kind is DateTime:
            assert column.type.timezone, (table.name, name)
        if nullable is not None:
            assert column.nullable is nullable, (table.name, name)
    assert [column.name for column in table.primary_key] == ["id"], table.name


def _foreign_keys(table) -> set:
    return {
        (
            tuple(constraint.column_keys),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in table.foreign_key_constraints
    }


def test_the_orm_declares_both_tables_with_their_columns_and_foreign_keys():
    proposal, request = _models()
    assert proposal.__tablename__ == PROPOSALS
    assert request.__tablename__ == REQUESTS
    _assert_columns(proposal.__table__, PROPOSAL_COLUMNS)
    _assert_columns(request.__table__, REQUEST_COLUMNS)
    assert _foreign_keys(proposal.__table__) == PROPOSAL_FOREIGN_KEYS
    assert _foreign_keys(request.__table__) == REQUEST_FOREIGN_KEYS
    (composite,) = (
        constraint
        for constraint in request.__table__.foreign_key_constraints
        if len(constraint.column_keys) == 3
    )
    assert composite.name == "fk_action_delivery_requests_proposal"


def test_the_frozen_lists():
    from backend.database import saas_models

    assert saas_models.ACTION_PROPOSAL_TYPES == ("crm_update", "ticket_create", "coaching_task")
    assert saas_models.ACTION_PROPOSAL_SOURCES == ("action_item", "review_finding")
    assert saas_models.ACTION_PROPOSAL_STATUSES == ("pending", "approved", "rejected", "revoked")
    assert saas_models.ACTION_PROPOSAL_OPEN_STATUSES == ("pending", "approved")
    assert saas_models.ACTION_PROPOSAL_REJECT_REASONS == (
        "not_needed",
        "incorrect",
        "duplicate",
        "other",
    )
    assert saas_models.ACTION_PROPOSAL_REVOKE_REASONS == (
        "approved_in_error",
        "no_longer_needed",
        "other",
    )


def _checks(model) -> dict[str, str]:
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in model.__table__.constraints
        if constraint.name and constraint.name.startswith("ck_")
    }


def test_the_checks_are_named_use_no_regex_and_never_name_a_user_column():
    proposal, request = _models()
    proposal_checks = _checks(proposal)
    request_checks = _checks(request)
    assert set(PROPOSAL_CHECKS) <= set(proposal_checks)
    assert set(REQUEST_CHECKS) <= set(request_checks)
    for column, pattern in REQUEST_CHECK_PATTERNS.items():
        assert any(re.search(pattern, name) for name in request_checks), column
    for checks, user_columns in (
        (proposal_checks, PROPOSAL_USER_COLUMNS),
        (request_checks, REQUEST_USER_COLUMNS),
    ):
        for name, check in checks.items():
            lowered = check.lower()
            assert "~" not in lowered and "regexp" not in lowered, name
            assert "similar" not in lowered and "glob" not in lowered, name
            for column in user_columns:
                assert column not in check, (name, column)


def _keys(table: str) -> dict[str, tuple[tuple[str, ...], bool]]:
    with inspection_session_factory()() as db:
        inspector = inspect(db.connection())
        found = {
            index["name"]: (tuple(index["column_names"]), bool(index["unique"]))
            for index in inspector.get_indexes(table)
        }
        for unique in inspector.get_unique_constraints(table):
            found[unique["name"]] = (tuple(unique["column_names"]), True)
        return found


def _index_sql(name: str) -> str:
    if _on_postgres():
        return _scalar("SELECT indexdef FROM pg_indexes WHERE indexname = :n", n=name)
    return _scalar("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = :n", n=name)


def test_the_schema_has_the_named_unique_keys_and_indexes(client):
    for table in TABLES:
        keys = _keys(table)
        for name, columns in UNIQUE_KEYS[table].items():
            assert keys.get(name) == (columns, True), (table, name, keys.get(name))
        for name, columns in PLAIN_INDEXES[table].items():
            assert name in keys, (table, name)
            assert keys[name][0] == columns, (table, name)
        plain = {name: value for name, value in keys.items() if name not in PARTIAL_INDEXES}
        for column in FK_LEADING_INDEXES[table]:
            assert any(columns[0] == column for columns, _ in plain.values()), (table, column)
    proposal_keys = _keys(PROPOSALS)
    for name, columns in PARTIAL_INDEXES.items():
        assert proposal_keys.get(name) == (columns, True), name
        sql = _index_sql(name)
        assert sql is not None, name
        head, where = re.split(r"\bWHERE\b", sql, maxsplit=1)
        assert "'pending'" in where and "'approved'" in where, sql
        assert "'rejected'" not in where and "'revoked'" not in where, sql
        assert re.search(rf"\b{columns[0]}\b\)? IS NOT NULL", where), sql


def test_both_tables_are_tenant_only_workspace_tables_and_requests_are_never_updated():
    for table in TABLES:
        assert table in WORKSPACE_TABLES, table
        assert table not in APPEND_ONLY_TABLES, table
        assert table not in NO_DELETE_TABLES, table
        assert table not in SHARED_CONTENT_TABLES, table
        assert table not in UNPROTECTED_TABLES, table
        assert POLICIES[table] == (f"workspace_id = {TENANT}", f"workspace_id = {TENANT}"), table
    assert PROPOSALS not in NO_UPDATE_TABLES
    assert REQUESTS in NO_UPDATE_TABLES


def _pg_shape(table: str) -> dict:
    with inspection_session_factory()() as db:
        columns = db.execute(
            text(
                "SELECT column_name, data_type, character_maximum_length, is_nullable, "
                "column_default FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = :t"
            ),
            {"t": table},
        ).all()
        constraints = db.execute(
            text(
                "SELECT conname, contype::text, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = to_regclass(:t)"
            ),
            {"t": f"public.{table}"},
        ).all()
        indexes = db.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :t"), {"t": table}
        ).all()
        policies = db.execute(
            text(
                "SELECT policyname, permissive, roles::text, cmd, qual, with_check "
                "FROM pg_policies WHERE tablename = :t"
            ),
            {"t": table},
        ).all()
        flags = db.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = to_regclass(:t)"
            ),
            {"t": f"public.{table}"},
        ).all()
        grants = db.execute(
            text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_schema = 'public' AND table_name = :t AND grantee = :r"
            ),
            {"t": table, "r": APP_ROLE},
        ).all()
    return {
        "columns": {name: tuple(rest) for name, *rest in columns},
        "constraints": {name: (kind, definition) for name, kind, definition in constraints},
        "indexes": dict(indexes),
        "policies": {name: tuple(rest) for name, *rest in policies},
        "rls": [tuple(row) for row in flags],
        "grants": sorted(row[0] for row in grants),
    }


EMPTY_SHAPE = {
    "columns": {},
    "constraints": {},
    "indexes": {},
    "policies": {},
    "rls": [],
    "grants": [],
}
PG_TYPES = {String: "character varying", JSON: "json", Integer: "integer"}
PG_GRANTS = {
    PROPOSALS: ["DELETE", "INSERT", "SELECT", "UPDATE"],
    REQUESTS: ["DELETE", "INSERT", "SELECT"],
}


def _pg_fk(column: str, target: str, action: str) -> str:
    return f"FOREIGN KEY ({column}) REFERENCES {target}(id) ON DELETE {action}"


PG_FOREIGN_KEYS = {
    PROPOSALS: sorted(
        [
            _pg_fk("workspace_id", "workspaces", "CASCADE"),
            _pg_fk("meeting_id", "meetings", "CASCADE"),
            _pg_fk("action_item_id", "action_items", "SET NULL"),
            _pg_fk("review_item_id", "review_items", "SET NULL"),
            _pg_fk("source_transcript_revision_id", "transcript_revisions", "CASCADE"),
            _pg_fk("target_external_record_id", "external_records", "RESTRICT"),
            *(_pg_fk(column, "users", "SET NULL") for column in PROPOSAL_USER_COLUMNS),
        ]
    ),
    REQUESTS: sorted(
        [
            _pg_fk("workspace_id", "workspaces", "CASCADE"),
            _pg_fk("meeting_id", "meetings", "CASCADE"),
            "FOREIGN KEY (proposal_id, workspace_id, meeting_id) REFERENCES "
            "action_proposals(id, workspace_id, meeting_id) ON DELETE CASCADE",
            _pg_fk("target_external_record_id", "external_records", "RESTRICT"),
            _pg_fk("approved_by_user_id", "users", "SET NULL"),
        ]
    ),
}


def _assert_pg_shape(table: str, expected_columns: dict) -> dict:
    shape = _pg_shape(table)
    assert set(shape["columns"]) == set(expected_columns), table
    for name, (kind, length, nullable) in expected_columns.items():
        data_type, size, is_nullable, _default = shape["columns"][name]
        wanted = "timestamp with time zone" if kind is DateTime else PG_TYPES[kind]
        assert (data_type, size) == (wanted, length), (table, name)
        if nullable is not None:
            assert is_nullable == ("YES" if nullable else "NO"), (table, name)
    constraints = shape["constraints"]
    foreign = sorted(definition for kind, definition in constraints.values() if kind == "f")
    assert foreign == PG_FOREIGN_KEYS[table], table
    checks = {name: definition for name, (kind, definition) in constraints.items() if kind == "c"}
    required = PROPOSAL_CHECKS if table == PROPOSALS else REQUEST_CHECKS
    assert set(required) <= set(checks), table
    if table == REQUESTS:
        assert constraints["fk_action_delivery_requests_proposal"][0] == "f"
        for column, pattern in REQUEST_CHECK_PATTERNS.items():
            assert any(re.search(pattern, name) for name in checks), column
    ((name, policy),) = shape["policies"].items()
    permissive, roles, command, using, check = policy
    assert (name, permissive, roles, command) == (POLICY_NAME, "PERMISSIVE", "{public}", "ALL")
    assert using == check
    assert "homayar.workspace_id" in using
    assert "share" not in using and "service" not in using
    assert shape["rls"] == [(True, True)]
    assert shape["grants"] == PG_GRANTS[table], table
    return shape


@pytest.mark.postgres
def test_on_postgres_the_orm_schema_has_both_tables_with_tenant_rls_and_grants(client):
    _requires_postgres()
    _models()
    _assert_pg_shape(PROPOSALS, PROPOSAL_COLUMNS)
    _assert_pg_shape(REQUESTS, REQUEST_COLUMNS)


def _privileges(table: str) -> dict[str, bool]:
    return {
        privilege: _scalar(
            "SELECT has_table_privilege(:r, :t, :p)", r=APP_ROLE, t=table, p=privilege
        )
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
    }


def _apply_as_owner() -> None:
    from backend.database.rls import apply_row_level_security

    with inspection_session_factory()() as db:
        try:
            apply_row_level_security(db.connection())
        except Exception:
            db.rollback()
            raise
        db.commit()


def _write_pair(db, conv: dict) -> dict:
    proposal, request = _models()
    row = proposal(**_approved(_ticket(conv)))
    db.add(row)
    db.flush()
    delivered = request(**_request(conv, row.id))
    db.add(delivered)
    db.flush()
    return conv | {"proposal_id": row.id, "request_id": delivered.id}


@pytest.mark.postgres
def test_the_app_role_never_updates_a_request_and_may_insert_and_delete_it(client, email_outbox):
    _requires_postgres()
    _models()
    full = {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": True}
    no_update = {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": True}
    assert _privileges(PROPOSALS) == full
    assert _privileges(REQUESTS) == no_update
    conv = _conversation(_tenant(client, email_outbox, "grants"), "grants")
    factory = get_session_factory()
    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=conv["workspace_id"])
        conv = _write_pair(db, conv)
        db.commit()
    with factory() as db:
        bind_tenant_context(db, workspace_id=conv["workspace_id"])
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(
                text(f"UPDATE {REQUESTS} SET payload_version = 2 WHERE id = :id"),  # noqa: S608
                {"id": conv["request_id"]},
            )
        db.rollback()
    with factory() as db:
        bind_tenant_context(db, workspace_id=conv["workspace_id"])
        updated = db.execute(
            text(
                f"UPDATE {PROPOSALS} SET status = 'revoked', revoked_at = :now, "  # noqa: S608
                "revoke_reason = 'no_longer_needed', revision = revision + 1 "
                "WHERE id = :id AND revision = 1 AND status = 'approved'"
            ),
            {"now": NOW, "id": conv["proposal_id"]},
        )
        assert updated.rowcount == 1
        db.commit()
    _apply_as_owner()
    assert _privileges(PROPOSALS) == full
    assert _privileges(REQUESTS) == no_update
    with factory() as db:
        bind_tenant_context(db, workspace_id=conv["workspace_id"])
        removed = db.execute(
            text(f"DELETE FROM {REQUESTS} WHERE id = :id"),  # noqa: S608
            {"id": conv["request_id"]},
        )
        assert removed.rowcount == 1
        db.commit()
    assert _ids(REQUESTS) == set()
    assert _ids(PROPOSALS) == {conv["proposal_id"]}


@pytest.mark.postgres
def test_a_session_reads_and_writes_only_its_own_workspace(client, email_outbox):
    _requires_postgres()
    proposal, request = _models()
    first = _conversation(_tenant(client, email_outbox, "tenant-a"), "tenant_a")
    second = _conversation(_tenant(client, email_outbox, "tenant-b"), "tenant_b")
    factory = get_session_factory()
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        first = _write_pair(db, first)
        db.commit()
    with factory() as db:
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        for table in TABLES:
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0  # noqa: S608
        for statement in (
            f"UPDATE {PROPOSALS} SET revision = revision + 1",  # noqa: S608
            f"DELETE FROM {REQUESTS}",  # noqa: S608
            f"DELETE FROM {PROPOSALS}",  # noqa: S608
        ):
            assert db.execute(text(statement)).rowcount == 0, statement
        second = _write_pair(db, second)
        db.commit()
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        for table in TABLES:
            visible = db.execute(text(f"SELECT workspace_id FROM {table}")).scalars()  # noqa: S608
            assert list(visible) == [first["workspace_id"]], table
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        db.add(proposal(**_ticket(_fresh_item(second))))
        with pytest.raises(DBAPIError, match="row-level security"):
            db.flush()
        db.rollback()
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        other = _proposal(_approved(_ticket(_fresh_item(second))))
        db.add(request(**_request(second, other)))
        with pytest.raises(DBAPIError, match="row-level security"):
            db.flush()
        db.rollback()
    with factory() as db:
        for table in TABLES:
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0  # noqa: S608
    assert len(_ids(PROPOSALS, workspace_id=first["workspace_id"])) == 1
    assert len(_ids(REQUESTS, workspace_id=second["workspace_id"])) == 1


@pytest.mark.postgres
def test_a_share_token_does_not_reach_the_rows(client, email_outbox):
    _requires_postgres()
    proposal, request = _models()
    conv = _conversation(_tenant(client, email_outbox, "share"), "share")
    proposal_id = _proposal(_approved(_ticket(conv)))
    _delivery(_request(conv, proposal_id))
    token_hash = "ef" * 32
    with inspection_session_factory()() as db:
        db.add(
            ShareLink(
                workspace_id=conv["workspace_id"],
                meeting_id=conv["meeting_id"],
                created_by_user_id=conv["user_id"],
                token_hash=token_hash,
                audience=ShareAudience.LINK,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        db.commit()
    with get_session_factory()() as db:
        bind_capability_context(db, share_token_hash=token_hash)
        assert db.scalars(select(Meeting.id)).all() == [conv["meeting_id"]]
        assert db.scalars(select(proposal.id)).all() == []
        assert db.scalars(select(request.id)).all() == []
        db.rollback()


def _alembic_config():
    from alembic.config import Config

    from backend.observability.release import PROJECT_ROOT

    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _new_script():
    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory.from_config(_alembic_config())
    children = [
        script for script in scripts.walk_revisions() if script.down_revision == BASE_REVISION
    ]
    assert len(children) == 1, [script.revision for script in children]
    return children[0]


def test_one_new_migration_follows_the_base_head_and_is_the_only_head():
    from alembic.script import ScriptDirectory

    from backend.observability.release import code_migration_head

    new = _new_script()
    assert ScriptDirectory.from_config(_alembic_config()).get_heads() == [new.revision]
    assert code_migration_head() == new.revision


def test_the_migration_freezes_its_checks_and_documents_itself():
    new = _new_script()
    source = Path(new.path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "saas_models" not in (node.module or ""), node.module
        if isinstance(node, ast.Import):
            assert all("saas_models" not in alias.name for alias in node.names)
    assert "apply_row_level_security" in source
    for name in (*PROPOSAL_CHECKS, *REQUEST_CHECKS):
        assert f'"{name}"' in source or f"'{name}'" in source, name
    doc = new.module.__doc__ or ""
    assert "HOM-135" in doc
    lowered = doc.lower()
    for words in ("no data changes", "downgrade", "purge", "grant"):
        assert words in lowered, words
    assert "row-level security" in lowered or "rls" in lowered
    assert "backup" in lowered or "reconcil" in lowered
    assert "—" not in doc


def _reflect(connection, table: str) -> dict:
    inspector = inspect(connection)
    return {
        "columns": [
            (column["name"], str(column["type"]), column["nullable"])
            for column in inspector.get_columns(table)
        ],
        "primary_key": inspector.get_pk_constraint(table)["constrained_columns"],
        "foreign_keys": sorted(
            (
                key["name"] or "",
                tuple(key["constrained_columns"]),
                key["referred_table"],
                tuple(key["referred_columns"]),
                key["options"].get("ondelete"),
            )
            for key in inspector.get_foreign_keys(table)
        ),
        "unique": sorted(
            (unique["name"] or "", tuple(unique["column_names"]))
            for unique in inspector.get_unique_constraints(table)
        ),
        "checks": sorted(
            (check["name"] or "", check["sqltext"])
            for check in inspector.get_check_constraints(table)
        ),
        "indexes": sorted(
            (index["name"], tuple(index["column_names"]), bool(index["unique"]))
            for index in inspector.get_indexes(table)
        ),
    }


def _objects(connection) -> set[tuple[str, str, str]]:
    return {
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT type, name, tbl_name FROM sqlite_master WHERE tbl_name <> 'alembic_version'"
            )
        ).all()
    }


def _partial_sql(connection) -> dict[str, str]:
    return {
        name: connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = :n"), {"n": name}
        ).scalar_one()
        for name in PARTIAL_INDEXES
    }


def _untouched(shapes: dict) -> dict:
    return {name: shape for name, shape in shapes.items() if name not in TABLES}


def test_on_sqlite_the_migration_builds_the_orm_tables_and_drops_only_them():
    new = _new_script()
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            orm = {table: _reflect(connection, table) for table in TABLES}
            orm_partial = _partial_sql(connection)
            for name in reversed(TABLES):
                Base.metadata.tables[name].drop(connection)
            before = {
                name: _reflect(connection, name) for name in inspect(connection).get_table_names()
            }
            before_objects = _objects(connection)
            with Operations.context(MigrationContext.configure(connection)):
                new.module.upgrade()
            assert {table: _reflect(connection, table) for table in TABLES} == orm
            assert _partial_sql(connection) == orm_partial
            after = {
                name: _reflect(connection, name) for name in inspect(connection).get_table_names()
            }
            assert _untouched(after) == _untouched(before)
            with Operations.context(MigrationContext.configure(connection)):
                new.module.downgrade()
            final = {
                name: _reflect(connection, name) for name in inspect(connection).get_table_names()
            }
            assert final == before
            assert _objects(connection) == before_objects
    finally:
        engine.dispose()


def _alembic_at(monkeypatch, url: str):
    from backend.config import reset_settings_cache

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    return _alembic_config()


def test_on_sqlite_alembic_upgrades_from_the_base_checks_clean_and_downgrades_one_step(
    tmp_path, monkeypatch
):
    from alembic import command
    from alembic.util.exc import AutogenerateDiffsDetected

    from backend.config import reset_settings_cache

    new = _new_script()
    url = f"sqlite:///{tmp_path / 'action-proposals.db'}"
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            orm = {table: _reflect(connection, table) for table in TABLES}
            for name in reversed(TABLES):
                Base.metadata.tables[name].drop(connection)
            before_objects = _objects(connection)
        config = _alembic_at(monkeypatch, url)
        command.stamp(config, BASE_REVISION)
        command.upgrade(config, new.revision)
        with engine.connect() as connection:
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            assert version.scalar() == new.revision
            assert {table: _reflect(connection, table) for table in TABLES} == orm
        try:
            command.check(config)
        except AutogenerateDiffsDetected as exc:
            pytest.fail(f"the migration does not match the ORM: {exc}")
        command.downgrade(config, "-1")
        with engine.connect() as connection:
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            assert version.scalar() == BASE_REVISION
            assert _objects(connection) == before_objects
    finally:
        engine.dispose()
        reset_settings_cache()


OTHER_TABLE_QUERIES = {
    "tables": (
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname NOT IN (:a, :b)"
    ),
    "columns": (
        "SELECT table_name, column_name, data_type, character_maximum_length, "
        "is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name NOT IN (:a, :b)"
    ),
    "constraints": (
        "SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid) "
        "FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = 'public' AND c.conrelid <> 0 "
        "AND c.conrelid::regclass::text NOT IN (:a, :b)"
    ),
    "indexes": (
        "SELECT tablename, indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename NOT IN (:a, :b)"
    ),
    "policies": (
        "SELECT tablename, policyname, permissive, roles::text, cmd, qual, with_check "
        "FROM pg_policies WHERE schemaname = 'public' AND tablename NOT IN (:a, :b)"
    ),
    "grants": (
        "SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema = 'public' AND table_name NOT IN (:a, :b)"
    ),
}


def _other_tables() -> dict:
    excluded = {"a": PROPOSALS, "b": REQUESTS}
    with inspection_session_factory()() as db:
        return {
            name: sorted(tuple(row) for row in db.execute(text(sql), excluded).all())
            for name, sql in OTHER_TABLE_QUERIES.items()
        }


KEPT_COUNTS = (
    "meetings",
    "action_items",
    "review_items",
    "transcript_revisions",
    "external_records",
)


@pytest.mark.postgres
def test_on_postgres_the_migration_creates_and_drops_only_the_two_tables(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    from alembic import command

    from backend.config import reset_settings_cache

    new = _new_script()
    tenant = _tenant(client, email_outbox, "migration")
    orm_shapes = {
        PROPOSALS: _assert_pg_shape(PROPOSALS, PROPOSAL_COLUMNS),
        REQUESTS: _assert_pg_shape(REQUESTS, REQUEST_COLUMNS),
    }
    conv = _conversation(tenant, "migration")
    _delivery(_crm_request(conv, _proposal(_approved(_crm(conv)))))
    counts = {table: _scalar(f"SELECT count(*) FROM {table}") for table in KEPT_COUNTS}  # noqa: S608
    config = _alembic_at(monkeypatch, settings.database_url)
    try:
        command.stamp(config, "head")
        head_others = _other_tables()
        command.downgrade(config, BASE_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        for table in TABLES:
            assert _scalar("SELECT to_regclass(:t) IS NULL", t=f"public.{table}"), table
            assert _pg_shape(table) == EMPTY_SHAPE, table
        assert _other_tables() == head_others
        for table, count in counts.items():
            assert _scalar(f"SELECT count(*) FROM {table}") == count, table  # noqa: S608

        command.upgrade(config, new.revision)
        assert _scalar("SELECT version_num FROM alembic_version") == new.revision
        assert {table: _pg_shape(table) for table in TABLES} == orm_shapes
        assert all(_ids(table) == set() for table in TABLES)
        assert _other_tables() == head_others
        for rules in RULES:
            rules(tenant)
        assert _ids(REQUESTS) != set()

        command.downgrade(config, BASE_REVISION)
        assert _other_tables() == head_others
        command.upgrade(config, "head")
        assert {table: _pg_shape(table) for table in TABLES} == orm_shapes
        assert _other_tables() == head_others
    finally:
        reset_settings_cache()


def _purge_setup(tenant: dict, key: str, *, meeting_id: str | None = None) -> dict:
    conv = _conversation(tenant, key, meeting_id=meeting_id)
    proposal_id = _proposal(_approved(_ticket(conv)))
    request_id = _delivery(_request(conv, proposal_id))
    finding_id = _proposal(_coaching(conv))
    return conv | {"proposal_ids": {proposal_id, finding_id}, "request_ids": {request_id}}


def test_the_purge_deletes_requests_then_proposals_before_their_sources_and_counts_them(
    client, email_outbox
):
    _models()
    tenant = _tenant(client, email_outbox, "purge-order")
    subject = _purge_setup(tenant, "purge_subject")
    kept = _purge_setup(tenant, "purge_kept")
    twin = _conversation(tenant, "purge_twin")
    statements: list[str] = []

    def record(_connection, _cursor, statement, *_args) -> None:
        statements.append(statement)

    factory = get_session_factory()
    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            meeting = db.get(Meeting, subject["meeting_id"])
            removed = _remove_content(db, workspace_id=tenant["workspace_id"], meeting=meeting)
            db.commit()
        finally:
            event.remove(engine, "before_cursor_execute", record)
    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        meeting = db.get(Meeting, twin["meeting_id"])
        baseline = _remove_content(db, workspace_id=tenant["workspace_id"], meeting=meeting)
        db.commit()

    deletes = [
        s.split()[2].strip('"') for s in statements if s.lstrip().upper().startswith("DELETE FROM")
    ]
    assert REQUESTS in deletes and PROPOSALS in deletes, deletes
    assert deletes.index(REQUESTS) < deletes.index(PROPOSALS), deletes
    for table in ("action_items", "review_items", "transcript_revisions"):
        assert deletes.index(PROPOSALS) < deletes.index(table), table
    assert removed == baseline + 3
    for table in TABLES:
        assert _ids(table, meeting_id=subject["meeting_id"]) == set(), table
    assert _ids(PROPOSALS, meeting_id=kept["meeting_id"]) == kept["proposal_ids"]
    assert _ids(REQUESTS, meeting_id=kept["meeting_id"]) == kept["request_ids"]


def test_the_conversation_purge_removes_only_its_conversations_rows(
    client, email_outbox, monkeypatch, settings
):
    _models()
    _patch_media(monkeypatch)
    tenant = _ledger_tenant(client, email_outbox, "action-oracle-purge")
    queued, kept, plain = [
        _processed(client, tenant, settings, key=f"action-oracle-purge-{index:04d}", index=index)
        for index in range(3)
    ]
    purged = _purge_setup(tenant, "route_purged", meeting_id=queued)
    survivor = _purge_setup(tenant, "route_kept", meeting_id=kept)
    for meeting_id in (queued, plain):
        _delete_conversation(client, tenant, meeting_id)
        assert _purge(meeting_id).state is PurgeState.COMPLETED
    for table in TABLES:
        assert _ids(table, meeting_id=queued) == set(), table
    assert _ids(PROPOSALS, meeting_id=kept) == survivor["proposal_ids"]
    assert _ids(REQUESTS, meeting_id=kept) == survivor["request_ids"]
    assert purged["proposal_ids"].isdisjoint(_ids(PROPOSALS))
    assert _purge(queued).content_rows_removed == _purge(plain).content_rows_removed + 5


def test_the_backup_reconciliation_counts_both_tables():
    for table in TABLES:
        assert table in RECONCILED_TABLES, table
        columns = Base.metadata.tables[table].c
        assert "meeting_id" in columns and "workspace_id" in columns


def _adr() -> str:
    from backend.observability.release import PROJECT_ROOT

    path = (
        PROJECT_ROOT.parent
        / "docs"
        / "architecture"
        / "decisions"
        / "0082-approval-bound-action-proposals.md"
    )
    assert path.exists(), f"no {path}"
    return path.read_text(encoding="utf-8")


def test_the_adr_is_numbered_and_built_like_the_others():
    content = _adr()
    assert content.startswith("# ADR 0082: ")
    assert "HOM-135" in content
    for heading in ("Context", "Decision", "Consequences"):
        assert re.search(rf"^## {heading}\s*$", content, re.MULTILINE), heading


def test_the_adr_uses_no_em_dash_and_no_double_hyphen_dash():
    content = _adr()
    assert "—" not in content
    assert not re.search(r"(^|\s)--(\s|$)", content, re.MULTILINE)


ADR_TOPICS = {
    "proposals_table": ("action_proposals",),
    "requests_table": ("action_delivery_requests",),
    "revoked_status": ("revoked",),
    "transcript_pin": ("source_transcript_revision_id",),
    "source_digest": ("source_digest",),
    "payload_hash": ("payload_hash",),
    "payload_version": ("payload_version",),
    "no_update_grant": ("NO_UPDATE",),
    "hom136": ("HOM-136",),
    "row_lock": ("FOR UPDATE",),
    "routes": ("/api/v1/action-proposals",),
    "module_gate": ("require_module",),
    "integrations_module": ("integrations",),
    "api_keys": ("API key",),
    "propose_permission": ("action:propose",),
    "approve_permission": ("action:approve",),
    "expected_revision": ("expected_revision",),
    "conflict": ("409",),
    "refresh": ("refresh",),
    "idempotency": ("idempotency_key",),
    "self_approval": ("self-approval", "Self-approval", "self approval", "Self approval"),
    "audit_ids_only": ("ids and codes", "Ids and codes", "payload text"),
    "alternatives": ("lternative",),
}


@pytest.mark.parametrize("spellings", ADR_TOPICS.values(), ids=ADR_TOPICS.keys())
def test_the_adr_records_the_design(spellings):
    content = _adr()
    assert any(spelling in content for spelling in spellings), spellings
