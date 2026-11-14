"""HOM-135 PR 1: action proposals, approved delivery requests and their one migration.

Two tables.  ``action_proposals``: one proposal previews one exact outbound payload
(a CRM update, a ticket or a coaching task) built from one source (an action item or
a review finding), pinned to the transcript revision and a digest of the source.
``action_delivery_requests``: the immutable copy of an approved payload that HOM-136
delivers.  Nothing writes the tables yet; PR 2 adds the service, the routes, the
permissions and the audit events.  The tests check:

* the ORM declares the columns, the named constraints, the keys and the indexes of
  the design, the frozen lists and the check strings, which use no regex and never
  name a user column;
* ``database/rls.py`` lists both tables as workspace tables; the proposals have the
  full grants, the delivery requests are in ``NO_UPDATE_TABLES``;
* every deny case of the SPEC is refused by its named constraint, and the closest
  allow case passes (SQLite and PostgreSQL);
* at most one open proposal per action item and action type, and per finding;
* one delivery request per proposal, one per idempotency key, and only in the
  proposal's own workspace and meeting;
* the ORM refuses any update of a delivery request;
* cascades: a conversation, a workspace, a proposal or a transcript revision takes
  the rows with it; a removed action item, review item or user leaves the rows with
  a NULL id; a named external record cannot be deleted;
* on SQLite, the migration builds the same tables as the ORM, and its downgrade
  removes them and nothing else;
* on PostgreSQL: the ORM schema and the migrated schema are the same; downgrade
  removes both tables and leaves every other table as it was; tenant isolation
  (also on write); a share-token context sees none of it; the application role may
  not UPDATE a delivery request;
* the conversation purge deletes and counts both tables before the rows whose
  cascades would remove them; the backup reconciliation counts them; the code has
  one migration head, this one; ADR 0082 records the design.
"""

from __future__ import annotations

import hashlib
import importlib
import re
from datetime import UTC, datetime, timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, delete, event, inspect, select, text
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
    ACTION_PROPOSAL_OPEN_STATUSES,
    ACTION_PROPOSAL_REJECT_REASONS,
    ACTION_PROPOSAL_REVOKE_REASONS,
    ACTION_PROPOSAL_SOURCES,
    ACTION_PROPOSAL_STATUSES,
    ACTION_PROPOSAL_TYPES,
    ActionDeliveryRequest,
    ActionDeliveryRequestImmutableError,
    ActionItem,
    ActionProposal,
    ExternalRecord,
    Meeting,
    PurgeState,
    ReviewItem,
    ScorecardResultRecord,
    ShareAudience,
    ShareLink,
    TranscriptRevision,
    TranscriptSource,
    User,
    Workspace,
)
from backend.database.tenant_context import bind_capability_context, bind_tenant_context
from backend.services.conversation_purge_service import ERROR_LEGAL_HOLD, _remove_content
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user, register_user
from backend.tests.test_ingestion_ledger import _headers, _patch_media
from backend.tests.test_ingestion_ledger import _tenant as _ledger_tenant
from backend.tests.test_retention_purge import _media, _processed, _purge, _review, _sweep
from backend.tests.test_review_decisions_migration import _item_values
from backend.tests.test_scorecard_results_migration import (
    _add,
    _delete_conversation,
    _parents,
    _result,
)

NEW_REVISION = "fc9730c5f039"
BASE_REVISION = "d3d1c7aa3cd8"  # HOM-129: privileged MFA and break-glass
HEAD_REVISION = NEW_REVISION  # this PR holds the migration slot
PROPOSALS = "action_proposals"
REQUESTS = "action_delivery_requests"
TABLES = (PROPOSALS, REQUESTS)
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
PAYLOAD_HASH = "ab" * 32
SOURCE_DIGEST = "cd" * 32
CRITERION = "greeting"

_HEX = "'0123456789abcdef'"
_KEY_RULE = (
    "length({c}) BETWEEN 2 AND 64 "
    "AND trim(substr({c}, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
    "AND trim({c}, 'abcdefghijklmnopqrstuvwxyz0123456789_') = ''"
)
TYPE_SOURCE = (
    "NOT ((action_type IN ('crm_update','ticket_create') AND source_type = 'review_finding') "
    "OR (action_type = 'coaching_task' AND source_type = 'action_item'))"
)
TARGET_SHAPE = (
    "(action_type <> 'crm_update' OR (target_system IS NOT NULL "
    "AND target_external_record_id IS NOT NULL)) "
    "AND (action_type <> 'ticket_create' OR (target_system IS NOT NULL "
    "AND target_external_record_id IS NULL)) "
    "AND (action_type <> 'coaching_task' OR (target_system IS NULL "
    "AND target_external_record_id IS NULL))"
)


def _hex(column: str) -> str:
    return f"length({column}) = 64 AND trim({column}, {_HEX}) = ''"


# The check strings, character for character (SPEC 2.1, 2.2 and Amendment 1, A1.2).
PROPOSAL_CHECKS = {
    "ck_action_proposals_action_type": (
        "action_type IN ('crm_update','ticket_create','coaching_task')"
    ),
    "ck_action_proposals_source_type": "source_type IN ('action_item','review_finding')",
    "ck_action_proposals_status": "status IN ('pending','approved','rejected','revoked')",
    "ck_action_proposals_revision": "revision >= 1",
    "ck_action_proposals_payload_version": "payload_version >= 1",
    "ck_action_proposals_payload_hash": _hex("payload_hash"),
    "ck_action_proposals_source_digest": _hex("source_digest"),
    "ck_action_proposals_criterion_key": (
        f"criterion_id IS NULL OR ({_KEY_RULE.format(c='criterion_id')})"
    ),
    "ck_action_proposals_target_system": (
        "target_system IS NULL OR (length(target_system) BETWEEN 1 AND 60 "
        "AND trim(substr(target_system, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
        "AND trim(target_system, 'abcdefghijklmnopqrstuvwxyz0123456789_.-') = '')"
    ),
    "ck_action_proposals_source_shape": (
        "(source_type <> 'action_item' OR (review_item_id IS NULL AND criterion_id IS NULL)) "
        "AND (source_type <> 'review_finding' OR (action_item_id IS NULL "
        "AND criterion_id IS NOT NULL))"
    ),
    "ck_action_proposals_type_source": TYPE_SOURCE,
    "ck_action_proposals_target_shape": TARGET_SHAPE,
    "ck_action_proposals_reject_reason": (
        "reject_reason IS NULL OR reject_reason IN ('not_needed','incorrect','duplicate','other')"
    ),
    "ck_action_proposals_revoke_reason": (
        "revoke_reason IS NULL OR revoke_reason IN ('approved_in_error','no_longer_needed','other')"
    ),
    "ck_action_proposals_pending_fields": (
        "status <> 'pending' OR (decided_at IS NULL AND reject_reason IS NULL "
        "AND revoked_at IS NULL AND revoke_reason IS NULL)"
    ),
    "ck_action_proposals_approved_fields": (
        "status <> 'approved' OR (decided_at IS NOT NULL AND reject_reason IS NULL "
        "AND revoked_at IS NULL AND revoke_reason IS NULL)"
    ),
    "ck_action_proposals_rejected_fields": (
        "status <> 'rejected' OR (decided_at IS NOT NULL AND reject_reason IS NOT NULL "
        "AND revoked_at IS NULL AND revoke_reason IS NULL)"
    ),
    "ck_action_proposals_revoked_fields": (
        "status <> 'revoked' OR (decided_at IS NOT NULL AND revoked_at IS NOT NULL "
        "AND revoke_reason IS NOT NULL AND reject_reason IS NULL)"
    ),
}
REQUEST_CHECKS = {
    "ck_action_delivery_requests_action_type": (
        "action_type IN ('crm_update','ticket_create','coaching_task')"
    ),
    "ck_action_delivery_requests_source_type": "source_type IN ('action_item','review_finding')",
    "ck_action_delivery_requests_payload_version": "payload_version >= 1",
    "ck_action_delivery_requests_payload_hash": _hex("payload_hash"),
    "ck_action_delivery_requests_source_digest": _hex("source_digest"),
    "ck_action_delivery_requests_idempotency_key": _hex("idempotency_key"),
    "ck_action_delivery_requests_criterion_key": (
        f"source_criterion_id IS NULL OR ({_KEY_RULE.format(c='source_criterion_id')})"
    ),
    "ck_action_delivery_requests_type_source": TYPE_SOURCE,
    "ck_action_delivery_requests_target_shape": TARGET_SHAPE,
}
OPEN_ITEM_WHERE = "status IN ('pending','approved') AND action_item_id IS NOT NULL"
OPEN_FINDING_WHERE = "status IN ('pending','approved') AND review_item_id IS NOT NULL"
# SQLite names the columns of a broken unique constraint, not the constraint.
UNIQUE_COLUMNS = {
    "uq_action_proposals_open_item": (PROPOSALS, ("action_item_id", "action_type")),
    "uq_action_proposals_open_finding": (PROPOSALS, ("review_item_id", "criterion_id")),
    "uq_action_delivery_requests_proposal": (REQUESTS, ("proposal_id",)),
    "uq_action_delivery_requests_idempotency_key": (REQUESTS, ("idempotency_key",)),
}
PROPOSAL_USER_COLUMNS = ("created_by_user_id", "decided_by_user_id", "revoked_by_user_id")
REQUEST_USER_COLUMNS = ("approved_by_user_id",)
USER_COLUMNS = PROPOSAL_USER_COLUMNS + REQUEST_USER_COLUMNS


def _on_postgres() -> bool:
    return is_postgres_url(configured_database_url())


def _requires_postgres() -> None:
    if not _on_postgres():
        pytest.skip("needs PostgreSQL")


def _scalar(sql: str, **params):
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


def _tenant(client, email_outbox, label: str) -> dict:
    credentials = register_user(client, email_outbox, email=f"proposals-{label}@example.com")
    session = login_user(client, credentials)
    return {"user_id": session["user"]["id"], "workspace_id": session["workspaces"][0]["id"]}


def _source(tenant: dict, key: str, *, meeting_id: str | None = None) -> dict:
    """A conversation with every source a proposal can name.

    Without ``meeting_id`` a new conversation and its current transcript revision are
    made; a processed conversation keeps its current revision.  On it: one action
    item, one scorecard result with a review item in review, and one external
    record of the workspace.
    """
    with inspection_session_factory()() as db:
        if meeting_id is None:
            meeting = Meeting(
                workspace_id=tenant["workspace_id"],
                created_by_user_id=tenant["user_id"],
                title="Proposal call",
            )
            db.add(meeting)
            db.flush()
            meeting_id = meeting.id
            db.add(
                TranscriptRevision(
                    workspace_id=tenant["workspace_id"],
                    meeting_id=meeting_id,
                    revision_number=1,
                    source=TranscriptSource.ASR,
                    reason="processing",
                    language="fa",
                    segment_count=0,
                    is_current=True,
                )
            )
        db.commit()
    parents = _parents(tenant, key=key, meeting_id=meeting_id)
    result_id = _add(ScorecardResultRecord, _result(parents))
    item_id = _add(ReviewItem, _item_values(parents, scorecard_result_id=result_id))
    action_item_id = _add(ActionItem, _action_item(parents))
    record_id = _add(ExternalRecord, _record(parents, f"C-{meeting_id}"))
    return {
        **tenant,
        "meeting_id": meeting_id,
        "revision_id": parents["transcript_revision_id"],
        "result_id": result_id,
        "review_item_id": item_id,
        "action_item_id": action_item_id,
        "record_id": record_id,
    }


def _action_item(setup: dict, **extra: object) -> dict[str, object]:
    return {
        "workspace_id": setup["workspace_id"],
        "meeting_id": setup["meeting_id"],
        "description": "Send the offer by Friday.",
        **extra,
    }


def _record(setup: dict, external_id: str) -> dict[str, object]:
    with inspection_session_factory()() as db:
        organization_id = db.get(Workspace, setup["workspace_id"]).organization_id
    return {
        "organization_id": organization_id,
        "workspace_id": setup["workspace_id"],
        "source_system": "zendesk",
        "record_type": "customer",
        "external_id": external_id,
    }


def _crm(setup: dict, **extra: object) -> dict[str, object]:
    """A valid pending CRM update from the setup's action item."""
    return {
        "workspace_id": setup["workspace_id"],
        "meeting_id": setup["meeting_id"],
        "action_type": "crm_update",
        "source_type": "action_item",
        "action_item_id": setup["action_item_id"],
        "review_item_id": None,
        "criterion_id": None,
        "source_transcript_revision_id": setup["revision_id"],
        "source_digest": SOURCE_DIGEST,
        "target_system": "zendesk",
        "target_external_record_id": setup["record_id"],
        "payload_json": {"record": "customer", "fields": {"next_step": "Send the offer."}},
        "payload_hash": PAYLOAD_HASH,
        "payload_version": 1,
        "status": "pending",
        "revision": 1,
        "created_by_user_id": setup["user_id"],
        **extra,
    }


def _ticket(setup: dict, **extra: object) -> dict[str, object]:
    """A valid pending ticket from the setup's action item: a system, no record."""
    values = _crm(setup, action_type="ticket_create", target_external_record_id=None)
    values["payload_json"] = {"title": "Follow up", "body": "Send the offer."}
    values.update(extra)
    return values


def _coaching(setup: dict, **extra: object) -> dict[str, object]:
    """A valid pending coaching task from a finding of the setup's review item."""
    values = _crm(
        setup,
        action_type="coaching_task",
        source_type="review_finding",
        action_item_id=None,
        review_item_id=setup["review_item_id"],
        criterion_id=CRITERION,
        target_system=None,
        target_external_record_id=None,
    )
    values["payload_json"] = {"instruction": "Practise the greeting."}
    values.update(extra)
    return values


def _approved(build, setup: dict, **extra: object) -> dict[str, object]:
    values = build(setup, status="approved", decided_at=NOW, decided_by_user_id=setup["user_id"])
    values.update(extra)
    return values


def _rejected(build, setup: dict, **extra: object) -> dict[str, object]:
    values = build(
        setup,
        status="rejected",
        decided_at=NOW,
        decided_by_user_id=setup["user_id"],
        reject_reason="not_needed",
    )
    values.update(extra)
    return values


def _revoked(build, setup: dict, **extra: object) -> dict[str, object]:
    values = _approved(
        build,
        setup,
        status="revoked",
        revoked_at=NOW + timedelta(minutes=5),
        revoked_by_user_id=setup["user_id"],
        revoke_reason="no_longer_needed",
    )
    values.update(extra)
    return values


def _proposal_on(tenant: dict, key: str, build=_crm, **extra: object) -> str:
    """A stored valid proposal on a fresh source setup of its own (one per source)."""
    return _add(ActionProposal, build(_source(tenant, key), **extra))


def _key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _request(setup: dict, proposal_id: str, **extra: object) -> dict[str, object]:
    """A valid delivery request for an approved CRM update of the setup."""
    return {
        "workspace_id": setup["workspace_id"],
        "meeting_id": setup["meeting_id"],
        "proposal_id": proposal_id,
        "action_type": "crm_update",
        "target_system": "zendesk",
        "target_external_record_id": setup["record_id"],
        "payload_json": {"record": "customer", "fields": {"next_step": "Send the offer."}},
        "payload_hash": PAYLOAD_HASH,
        "payload_version": 1,
        "source_type": "action_item",
        "source_id": setup["action_item_id"],
        "source_criterion_id": None,
        "source_transcript_revision_id": setup["revision_id"],
        "source_digest": SOURCE_DIGEST,
        "idempotency_key": _key(setup["workspace_id"], proposal_id, PAYLOAD_HASH),
        "approved_by_user_id": setup["user_id"],
        "approved_at": NOW,
        **extra,
    }


def _approved_on(tenant: dict, key: str) -> dict:
    """A source setup with an approved CRM update, and no request yet."""
    setup = _source(tenant, key)
    proposal_id = _add(ActionProposal, _approved(_crm, setup))
    return setup | {"proposal_id": proposal_id}


def _names(constraint: str) -> str:
    if constraint.startswith("fk_") and not _on_postgres():
        # SQLite does not name a broken foreign key.  Each deny names rows that exist,
        # so only the composite key can refuse it.
        return "FOREIGN KEY constraint failed"
    if constraint in UNIQUE_COLUMNS and not _on_postgres():
        table, columns = UNIQUE_COLUMNS[constraint]
        return re.escape(", ".join(f"{table}.{column}" for column in columns))
    return rf"\b{constraint}\b"


def _refused(model, values: dict[str, object], constraint: str) -> None:
    """The insert fails on ``constraint``, and on no other rule first."""
    with inspection_session_factory()() as db:
        db.add(model(**values))
        with pytest.raises(IntegrityError, match=_names(constraint)):
            db.flush()
        db.rollback()


def _ids(table: str, **where: str) -> set[str]:
    clauses = " AND ".join(f"{column} = :{column}" for column in where) or "1 = 1"
    with inspection_session_factory()() as db:
        return set(
            db.execute(text(f"SELECT id FROM {table} WHERE {clauses}"), where).scalars()  # noqa: S608
        )


# --- the deny and allow cases (SPEC section 3) ----------------------------------------------


def _assert_action_type_rules(tenant: dict) -> None:
    setup = _source(tenant, "action_type")
    for value in ("email", "CRM_update", "ticket_create ", ""):
        _refused(
            ActionProposal, _ticket(setup, action_type=value), "ck_action_proposals_action_type"
        )
    _proposal_on(tenant, "action_type_ticket", _ticket)
    _proposal_on(tenant, "action_type_crm", _crm)
    _proposal_on(tenant, "action_type_coaching", _coaching)


def _assert_source_type_rules(tenant: dict) -> None:
    setup = _source(tenant, "source_type")
    for value in ("summary", "Action_item", ""):
        _refused(ActionProposal, _crm(setup, source_type=value), "ck_action_proposals_source_type")
    _proposal_on(tenant, "source_type_item", _crm, source_type="action_item")


def _assert_status_list_rules(tenant: dict) -> None:
    setup = _source(tenant, "status_list")
    for value in ("done", "Pending", "pending ", ""):
        _refused(ActionProposal, _crm(setup, status=value), "ck_action_proposals_status")
    _proposal_on(tenant, "status_pending", _crm, status="pending")


def _assert_counter_rules(tenant: dict) -> None:
    setup = _source(tenant, "counters")
    _refused(ActionProposal, _crm(setup, revision=0), "ck_action_proposals_revision")
    _refused(ActionProposal, _crm(setup, payload_version=0), "ck_action_proposals_payload_version")
    _proposal_on(tenant, "counters_one", _crm, revision=1, payload_version=1)
    _proposal_on(tenant, "counters_two", _crm, revision=2, payload_version=3)


BAD_HEX = ("a" * 63, "AB" * 32, "zz" * 32, "g" + "a" * 63)


def _assert_hex_rules(tenant: dict) -> None:
    setup = _source(tenant, "hex")
    for value in BAD_HEX:
        _refused(
            ActionProposal, _crm(setup, payload_hash=value), "ck_action_proposals_payload_hash"
        )
        _refused(
            ActionProposal, _crm(setup, source_digest=value), "ck_action_proposals_source_digest"
        )
    _proposal_on(tenant, "hex_valid", _crm, payload_hash="0" * 64, source_digest="f" * 64)


def _assert_source_shape_rules(tenant: dict) -> None:
    setup = _source(tenant, "source_shape")
    # An action item source names no criterion and no review item.
    _refused(
        ActionProposal, _crm(setup, criterion_id=CRITERION), "ck_action_proposals_source_shape"
    )
    _refused(
        ActionProposal,
        _crm(setup, review_item_id=setup["review_item_id"]),
        "ck_action_proposals_source_shape",
    )
    # A finding names no action item, and always its criterion.
    _refused(
        ActionProposal,
        _coaching(setup, action_item_id=setup["action_item_id"]),
        "ck_action_proposals_source_shape",
    )
    _refused(
        ActionProposal, _coaching(setup, criterion_id=None), "ck_action_proposals_source_shape"
    )
    # Allow-controls: the criterion NULL for an item; a review item and a criterion for a
    # finding.  A removed source (a NULL id) is a valid row; PR 2 treats it as stale.
    _proposal_on(tenant, "source_shape_item", _crm, criterion_id=None)
    _proposal_on(tenant, "source_shape_finding", _coaching)
    _proposal_on(tenant, "source_shape_removed_item", _crm, action_item_id=None)
    _proposal_on(tenant, "source_shape_removed_finding", _coaching, review_item_id=None)


def _assert_type_source_rules(tenant: dict) -> None:
    setup = _source(tenant, "type_source")
    # A CRM update or a ticket from a finding.
    finding = {
        "source_type": "review_finding",
        "action_item_id": None,
        "review_item_id": setup["review_item_id"],
        "criterion_id": CRITERION,
    }
    _refused(ActionProposal, _crm(setup, **finding), "ck_action_proposals_type_source")
    _refused(ActionProposal, _ticket(setup, **finding), "ck_action_proposals_type_source")
    # A coaching task from an action item.
    _refused(
        ActionProposal,
        _coaching(
            setup,
            source_type="action_item",
            action_item_id=setup["action_item_id"],
            review_item_id=None,
            criterion_id=None,
        ),
        "ck_action_proposals_type_source",
    )
    _proposal_on(tenant, "type_source_crm", _crm)
    _proposal_on(tenant, "type_source_coaching", _coaching)


def _assert_target_shape_rules(tenant: dict) -> None:
    setup = _source(tenant, "target_shape")
    rule = "ck_action_proposals_target_shape"
    _refused(ActionProposal, _crm(setup, target_external_record_id=None), rule)
    _refused(ActionProposal, _crm(setup, target_system=None), rule)
    _refused(ActionProposal, _ticket(setup, target_external_record_id=setup["record_id"]), rule)
    _refused(ActionProposal, _ticket(setup, target_system=None), rule)
    _refused(ActionProposal, _coaching(setup, target_system="zendesk"), rule)
    _refused(ActionProposal, _coaching(setup, target_external_record_id=setup["record_id"]), rule)
    _proposal_on(tenant, "target_shape_crm", _crm)
    _proposal_on(tenant, "target_shape_ticket", _ticket)
    _proposal_on(tenant, "target_shape_coaching", _coaching)


def _assert_target_system_rules(tenant: dict) -> None:
    setup = _source(tenant, "target_system")
    # Values within the column length: a longer one is a length error on PostgreSQL.
    for value in ("Bad Slug", "Zendesk", "1crm", "", "zen desk"):
        _refused(
            ActionProposal, _ticket(setup, target_system=value), "ck_action_proposals_target_system"
        )
    _proposal_on(tenant, "target_system_zendesk", _ticket, target_system="zendesk")
    _proposal_on(tenant, "target_system_dotted", _ticket, target_system="odoo.crm-2_x")


def _assert_criterion_key_rules(tenant: dict) -> None:
    setup = _source(tenant, "criterion_key")
    for value in ("9x", "Greeting", "g", "greet-ing"):
        _refused(
            ActionProposal,
            _coaching(setup, criterion_id=value),
            "ck_action_proposals_criterion_key",
        )
    _proposal_on(tenant, "criterion_key_valid", _coaching, criterion_id="greeting")


def _assert_reason_rules(tenant: dict) -> None:
    setup = _source(tenant, "reasons")
    for value in ("spam", "Other", ""):
        _refused(
            ActionProposal,
            _rejected(_crm, setup, reject_reason=value),
            "ck_action_proposals_reject_reason",
        )
    for value in ("x", "Other", ""):
        _refused(
            ActionProposal,
            _revoked(_crm, setup, revoke_reason=value),
            "ck_action_proposals_revoke_reason",
        )
    # Allow-controls: every listed reason (a rejected or revoked proposal is not open,
    # so the same item takes them all).
    for value in ACTION_PROPOSAL_REJECT_REASONS:
        _add(ActionProposal, _rejected(_crm, setup, reject_reason=value))
    for value in ACTION_PROPOSAL_REVOKE_REASONS:
        _add(ActionProposal, _revoked(_crm, setup, revoke_reason=value))


def _assert_pending_fields_rules(tenant: dict) -> None:
    setup = _source(tenant, "pending_fields")
    rule = "ck_action_proposals_pending_fields"
    for column, value in (
        ("decided_at", NOW),
        ("reject_reason", "other"),
        ("revoked_at", NOW),
        ("revoke_reason", "other"),
    ):
        _refused(ActionProposal, _crm(setup, **{column: value}), rule)
    _proposal_on(tenant, "pending_fields_valid", _crm, decided_at=None)


def _assert_approved_fields_rules(tenant: dict) -> None:
    setup = _source(tenant, "approved_fields")
    rule = "ck_action_proposals_approved_fields"
    _refused(ActionProposal, _approved(_crm, setup, decided_at=None), rule)
    for column, value in (
        ("reject_reason", "other"),
        ("revoked_at", NOW),
        ("revoke_reason", "other"),
    ):
        _refused(ActionProposal, _approved(_crm, setup, **{column: value}), rule)
    _add(ActionProposal, _approved(_crm, _source(tenant, "approved_fields_valid")))


def _assert_rejected_fields_rules(tenant: dict) -> None:
    setup = _source(tenant, "rejected_fields")
    rule = "ck_action_proposals_rejected_fields"
    _refused(ActionProposal, _rejected(_crm, setup, reject_reason=None), rule)
    _refused(ActionProposal, _rejected(_crm, setup, decided_at=None), rule)
    _refused(ActionProposal, _rejected(_crm, setup, revoked_at=NOW), rule)
    _refused(ActionProposal, _rejected(_crm, setup, revoke_reason="other"), rule)
    _add(ActionProposal, _rejected(_crm, setup, reject_reason="duplicate"))


def _assert_revoked_fields_rules(tenant: dict) -> None:
    setup = _source(tenant, "revoked_fields")
    rule = "ck_action_proposals_revoked_fields"
    _refused(ActionProposal, _revoked(_crm, setup, revoked_at=None), rule)
    _refused(ActionProposal, _revoked(_crm, setup, revoke_reason=None), rule)
    _refused(ActionProposal, _revoked(_crm, setup, decided_at=None), rule)
    _refused(ActionProposal, _revoked(_crm, setup, reject_reason="other"), rule)
    _add(ActionProposal, _revoked(_crm, setup))


def _assert_request_rules(tenant: dict) -> None:
    setup = _approved_on(tenant, "request_rules")
    proposal_id = setup["proposal_id"]
    for value in ("email", ""):
        _refused(
            ActionDeliveryRequest,
            _request(setup, proposal_id, action_type=value),
            "ck_action_delivery_requests_action_type",
        )
    _refused(
        ActionDeliveryRequest,
        _request(setup, proposal_id, source_type="summary"),
        "ck_action_delivery_requests_source_type",
    )
    _refused(
        ActionDeliveryRequest,
        _request(setup, proposal_id, payload_version=0),
        "ck_action_delivery_requests_payload_version",
    )
    for column in ("payload_hash", "source_digest", "idempotency_key"):
        for value in BAD_HEX:
            _refused(
                ActionDeliveryRequest,
                _request(setup, proposal_id, **{column: value}),
                f"ck_action_delivery_requests_{column}",
            )
    _refused(
        ActionDeliveryRequest,
        _request(setup, proposal_id, target_external_record_id=None),
        "ck_action_delivery_requests_target_shape",
    )
    _refused(
        ActionDeliveryRequest,
        _request(
            setup,
            proposal_id,
            action_type="coaching_task",
            source_type="review_finding",
            source_criterion_id=CRITERION,
        ),
        "ck_action_delivery_requests_target_shape",
    )
    _refused(
        ActionDeliveryRequest,
        _request(setup, proposal_id, source_type="review_finding", source_criterion_id=CRITERION),
        "ck_action_delivery_requests_type_source",
    )
    _refused(
        ActionDeliveryRequest,
        _request(setup, proposal_id, source_criterion_id="9x"),
        "ck_action_delivery_requests_criterion_key",
    )
    # Allow-control: the valid request of the approved proposal.
    _add(ActionDeliveryRequest, _request(setup, proposal_id))
    # A coaching task's request: internal, from a finding, with its criterion.
    finding = _source(tenant, "request_rules_finding")
    coaching_id = _add(ActionProposal, _approved(_coaching, finding))
    _add(
        ActionDeliveryRequest,
        _request(
            finding,
            coaching_id,
            action_type="coaching_task",
            target_system=None,
            target_external_record_id=None,
            source_type="review_finding",
            source_id=finding["review_item_id"],
            source_criterion_id=CRITERION,
        ),
    )


RULES = (
    _assert_action_type_rules,
    _assert_source_type_rules,
    _assert_status_list_rules,
    _assert_counter_rules,
    _assert_hex_rules,
    _assert_source_shape_rules,
    _assert_type_source_rules,
    _assert_target_shape_rules,
    _assert_target_system_rules,
    _assert_criterion_key_rules,
    _assert_reason_rules,
    _assert_pending_fields_rules,
    _assert_approved_fields_rules,
    _assert_rejected_fields_rules,
    _assert_revoked_fields_rules,
    _assert_request_rules,
)


@pytest.mark.parametrize("rules", RULES, ids=lambda rules: rules.__name__.removeprefix("_assert_"))
def test_the_database_refuses_the_deny_cases_and_takes_the_allow_cases(client, email_outbox, rules):
    rules(_tenant(client, email_outbox, "rules"))


# --- one open proposal per source, one request per proposal and its scope ------------------


def test_at_most_one_open_proposal_per_action_item_and_type(client, email_outbox):
    tenant = _tenant(client, email_outbox, "open-item")
    rule = "uq_action_proposals_open_item"
    # A second pending CRM update for the same action item.
    pending = _source(tenant, "open_item_pending")
    _add(ActionProposal, _crm(pending))
    _refused(ActionProposal, _crm(pending), rule)
    _refused(ActionProposal, _approved(_crm, pending), rule)
    # An approved one blocks a new one as well.
    approved = _source(tenant, "open_item_approved")
    _add(ActionProposal, _approved(_crm, approved))
    _refused(ActionProposal, _crm(approved), rule)
    # A rejected or revoked one does not.
    rejected = _source(tenant, "open_item_rejected")
    _add(ActionProposal, _rejected(_crm, rejected))
    _add(ActionProposal, _crm(rejected))
    revoked = _source(tenant, "open_item_revoked")
    _add(ActionProposal, _revoked(_crm, revoked))
    _add(ActionProposal, _crm(revoked))
    # The other action type on the same item is another proposal.
    _add(ActionProposal, _ticket(pending))
    # A removed source (NULL) never blocks: two open proposals without an item.
    _add(ActionProposal, _crm(pending, action_item_id=None))
    _add(ActionProposal, _crm(pending, action_item_id=None))


def test_at_most_one_open_proposal_per_finding(client, email_outbox):
    tenant = _tenant(client, email_outbox, "open-finding")
    rule = "uq_action_proposals_open_finding"
    setup = _source(tenant, "open_finding")
    _add(ActionProposal, _coaching(setup))
    _refused(ActionProposal, _coaching(setup), rule)
    _refused(ActionProposal, _approved(_coaching, setup), rule)
    # Another criterion of the same review item is another finding.
    _add(ActionProposal, _coaching(setup, criterion_id="closing"))
    # A rejected proposal does not block a new one.
    rejected = _source(tenant, "open_finding_rejected")
    _add(ActionProposal, _rejected(_coaching, rejected))
    _add(ActionProposal, _coaching(rejected))
    # A removed review item (NULL) never blocks.
    _add(ActionProposal, _coaching(setup, review_item_id=None))
    _add(ActionProposal, _coaching(setup, review_item_id=None))


def test_one_request_per_proposal_and_per_idempotency_key(client, email_outbox):
    tenant = _tenant(client, email_outbox, "one-request")
    first = _approved_on(tenant, "one_request_first")
    second = _approved_on(tenant, "one_request_second")
    _add(ActionDeliveryRequest, _request(first, first["proposal_id"]))
    # A second request for the same proposal, under another key.
    _refused(
        ActionDeliveryRequest,
        _request(first, first["proposal_id"], idempotency_key="e" * 64),
        "uq_action_delivery_requests_proposal",
    )
    # Another proposal with the first one's key.
    _refused(
        ActionDeliveryRequest,
        _request(
            second,
            second["proposal_id"],
            idempotency_key=_key(first["workspace_id"], first["proposal_id"], PAYLOAD_HASH),
        ),
        "uq_action_delivery_requests_idempotency_key",
    )
    # Allow-control: another proposal with its own key.
    _add(ActionDeliveryRequest, _request(second, second["proposal_id"]))


def test_a_request_stays_in_its_proposals_workspace_and_meeting(client, email_outbox):
    tenant = _tenant(client, email_outbox, "request-scope-a")
    other = _tenant(client, email_outbox, "request-scope-b")
    setup = _approved_on(tenant, "request_scope")
    rule = "fk_action_delivery_requests_proposal"
    # Another meeting of the same workspace.
    elsewhere = _source(tenant, "request_scope_elsewhere")
    _refused(
        ActionDeliveryRequest,
        _request(setup, setup["proposal_id"], meeting_id=elsewhere["meeting_id"]),
        rule,
    )
    # Another workspace, with a meeting of that workspace.
    foreign = _source(other, "request_scope_foreign")
    _refused(
        ActionDeliveryRequest,
        _request(
            setup,
            setup["proposal_id"],
            workspace_id=other["workspace_id"],
            meeting_id=foreign["meeting_id"],
        ),
        rule,
    )
    # A proposal that does not exist.
    _refused(ActionDeliveryRequest, _request(setup, "0" * 36), rule)
    # Allow-control: its own workspace and meeting.
    _add(ActionDeliveryRequest, _request(setup, setup["proposal_id"]))


def test_the_orm_refuses_to_update_a_request(client, email_outbox):
    tenant = _tenant(client, email_outbox, "immutable")
    setup = _approved_on(tenant, "immutable")
    request_id = _add(ActionDeliveryRequest, _request(setup, setup["proposal_id"]))
    for column, value in (
        ("payload_hash", "0" * 64),
        ("payload_json", {"changed": True}),
        ("approved_at", NOW + timedelta(hours=1)),
        ("target_system", "odoo"),
    ):
        with inspection_session_factory()() as db:
            row = db.get(ActionDeliveryRequest, request_id)
            setattr(row, column, value)
            with pytest.raises(ActionDeliveryRequestImmutableError, match=column):
                db.flush()
            db.rollback()
    with inspection_session_factory()() as db:
        row = db.get(ActionDeliveryRequest, request_id)
        assert row.payload_hash == PAYLOAD_HASH and row.target_system == "zendesk"
        # An unchanged flush is not an update; a delete is allowed (the purge and the
        # proposal cascade remove requests).
        db.flush()
        db.delete(row)
        db.commit()
    assert _ids(REQUESTS) == set()


# --- the ORM, the frozen lists and the RLS lists --------------------------------------------

PROPOSAL_COLUMNS = [
    "id",
    "workspace_id",
    "meeting_id",
    "action_type",
    "source_type",
    "action_item_id",
    "review_item_id",
    "criterion_id",
    "source_transcript_revision_id",
    "source_digest",
    "target_system",
    "target_external_record_id",
    "payload_json",
    "payload_hash",
    "payload_version",
    "status",
    "revision",
    "created_by_user_id",
    "decided_by_user_id",
    "revoked_by_user_id",
    "decided_at",
    "revoked_at",
    "reject_reason",
    "revoke_reason",
    "created_at",
    "updated_at",
]
PROPOSAL_LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "meeting_id": 36,
    "action_type": 20,
    "source_type": 20,
    "action_item_id": 36,
    "review_item_id": 36,
    "criterion_id": 64,
    "source_transcript_revision_id": 36,
    "source_digest": 64,
    "target_system": 60,
    "target_external_record_id": 36,
    "payload_hash": 64,
    "status": 20,
    "created_by_user_id": 36,
    "decided_by_user_id": 36,
    "revoked_by_user_id": 36,
    "reject_reason": 30,
    "revoke_reason": 30,
}
PROPOSAL_NULLABLE = {
    "action_item_id",
    "review_item_id",
    "criterion_id",
    "target_system",
    "target_external_record_id",
    "created_by_user_id",
    "decided_by_user_id",
    "revoked_by_user_id",
    "decided_at",
    "revoked_at",
    "reject_reason",
    "revoke_reason",
}
PROPOSAL_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("meeting_id",), ("meetings.id",), "CASCADE"),
    (None, ("action_item_id",), ("action_items.id",), "SET NULL"),
    (None, ("review_item_id",), ("review_items.id",), "SET NULL"),
    (None, ("source_transcript_revision_id",), ("transcript_revisions.id",), "CASCADE"),
    (None, ("target_external_record_id",), ("external_records.id",), "RESTRICT"),
    (None, ("created_by_user_id",), ("users.id",), "SET NULL"),
    (None, ("decided_by_user_id",), ("users.id",), "SET NULL"),
    (None, ("revoked_by_user_id",), ("users.id",), "SET NULL"),
}
PROPOSAL_INDEXES = {
    "ix_action_proposals_workspace_id": (["workspace_id"], False),
    "ix_action_proposals_action_item_id": (["action_item_id"], False),
    "ix_action_proposals_review_item_id": (["review_item_id"], False),
    "ix_action_proposals_source_transcript_revision_id": (
        ["source_transcript_revision_id"],
        False,
    ),
    "ix_action_proposals_target_external_record_id": (["target_external_record_id"], False),
    "uq_action_proposals_open_item": (["action_item_id", "action_type"], True),
    "uq_action_proposals_open_finding": (["review_item_id", "criterion_id"], True),
    "ix_action_proposals_queue": (["workspace_id", "status", "created_at", "id"], False),
    "ix_action_proposals_workspace_meeting": (["workspace_id", "meeting_id"], False),
}
REQUEST_COLUMNS = [
    "id",
    "workspace_id",
    "meeting_id",
    "proposal_id",
    "action_type",
    "target_system",
    "target_external_record_id",
    "payload_json",
    "payload_hash",
    "payload_version",
    "source_type",
    "source_id",
    "source_criterion_id",
    "source_transcript_revision_id",
    "source_digest",
    "idempotency_key",
    "approved_by_user_id",
    "approved_at",
    "created_at",
]
REQUEST_LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "meeting_id": 36,
    "proposal_id": 36,
    "action_type": 20,
    "target_system": 60,
    "target_external_record_id": 36,
    "payload_hash": 64,
    "source_type": 20,
    "source_id": 36,
    "source_criterion_id": 64,
    "source_transcript_revision_id": 36,
    "source_digest": 64,
    "idempotency_key": 64,
    "approved_by_user_id": 36,
}
REQUEST_NULLABLE = {
    "target_system",
    "target_external_record_id",
    "source_criterion_id",
    "approved_by_user_id",
}
REQUEST_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("meeting_id",), ("meetings.id",), "CASCADE"),
    (None, ("target_external_record_id",), ("external_records.id",), "RESTRICT"),
    (None, ("approved_by_user_id",), ("users.id",), "SET NULL"),
    (
        "fk_action_delivery_requests_proposal",
        ("proposal_id", "workspace_id", "meeting_id"),
        ("action_proposals.id", "action_proposals.workspace_id", "action_proposals.meeting_id"),
        "CASCADE",
    ),
}
REQUEST_UNIQUES = {
    "uq_action_delivery_requests_proposal": ("proposal_id",),
    "uq_action_delivery_requests_idempotency_key": ("idempotency_key",),
    "uq_action_delivery_requests_scope": ("id", "workspace_id", "meeting_id"),
}
REQUEST_INDEXES = {
    "ix_action_delivery_requests_workspace_id": (["workspace_id"], False),
    "ix_action_delivery_requests_target_external_record_id": (
        ["target_external_record_id"],
        False,
    ),
    "ix_action_delivery_requests_workspace_meeting": (["workspace_id", "meeting_id"], False),
    "ix_action_delivery_requests_approved": (["workspace_id", "approved_at", "id"], False),
}


def _declared(table) -> dict:
    named = {constraint.name: constraint for constraint in table.constraints if constraint.name}
    return {
        "foreign_keys": {
            (
                constraint.name,
                tuple(constraint.column_keys),
                tuple(element.target_fullname for element in constraint.elements),
                constraint.ondelete,
            )
            for constraint in table.foreign_key_constraints
        },
        "checks": {name: str(c.sqltext) for name, c in named.items() if name.startswith("ck_")},
        "uniques": {
            name: tuple(column.name for column in c.columns)
            for name, c in named.items()
            if name.startswith("uq_")
        },
        "indexes": {
            index.name: ([column.name for column in index.columns], index.unique)
            for index in table.indexes
        },
    }


def test_the_orm_declares_the_action_proposals_table():
    table = ActionProposal.__table__
    assert table.name == PROPOSALS
    assert [column.name for column in table.columns] == PROPOSAL_COLUMNS
    for name, length in PROPOSAL_LENGTHS.items():
        assert table.c[name].type.length == length, name
    for name in ("decided_at", "revoked_at", "created_at", "updated_at"):
        assert table.c[name].type.timezone, name
    assert table.c.payload_json.type.__class__.__name__ == "JSON"
    for name in ("payload_version", "revision"):
        assert table.c[name].type.__class__.__name__ == "Integer", name
    assert {column.name for column in table.columns if column.nullable} == PROPOSAL_NULLABLE
    assert [column.name for column in table.primary_key] == ["id"]
    assert table.c.id.default.arg.__name__ == "new_id"
    assert table.c.status.default.arg == "pending"
    assert table.c.revision.default.arg == 1
    assert table.c.payload_version.default.arg == 1
    for name in ("created_at", "updated_at"):
        assert table.c[name].default.arg.__name__ == "utcnow", name
    assert all(column.server_default is None for column in table.columns)

    declared = _declared(table)
    assert declared["foreign_keys"] == PROPOSAL_FOREIGN_KEYS
    assert declared["checks"] == PROPOSAL_CHECKS
    assert declared["uniques"] == {
        "uq_action_proposals_scope": ("id", "workspace_id", "meeting_id")
    }
    assert declared["indexes"] == PROPOSAL_INDEXES
    for name, where in (
        ("uq_action_proposals_open_item", OPEN_ITEM_WHERE),
        ("uq_action_proposals_open_finding", OPEN_FINDING_WHERE),
    ):
        (index,) = (index for index in table.indexes if index.name == name)
        for dialect in ("sqlite", "postgresql"):
            assert str(index.dialect_options[dialect]["where"]) == where, (name, dialect)
    assert "HOM-135" in ActionProposal.__doc__
    assert "compare-and-set" in ActionProposal.__doc__


def test_the_orm_declares_the_action_delivery_requests_table():
    table = ActionDeliveryRequest.__table__
    assert table.name == REQUESTS
    assert [column.name for column in table.columns] == REQUEST_COLUMNS
    for name, length in REQUEST_LENGTHS.items():
        assert table.c[name].type.length == length, name
    for name in ("approved_at", "created_at"):
        assert table.c[name].type.timezone, name
    assert table.c.payload_json.type.__class__.__name__ == "JSON"
    assert table.c.payload_version.type.__class__.__name__ == "Integer"
    assert {column.name for column in table.columns if column.nullable} == REQUEST_NULLABLE
    assert [column.name for column in table.primary_key] == ["id"]
    assert table.c.id.default.arg.__name__ == "new_id"
    assert table.c.created_at.default.arg.__name__ == "utcnow"
    for name in ("approved_at", "payload_version", "idempotency_key"):
        assert table.c[name].default is None, name
    assert all(column.server_default is None for column in table.columns)

    declared = _declared(table)
    assert declared["foreign_keys"] == REQUEST_FOREIGN_KEYS
    assert declared["checks"] == REQUEST_CHECKS
    assert declared["uniques"] == REQUEST_UNIQUES
    assert declared["indexes"] == REQUEST_INDEXES
    # Plain strings, no foreign key: the copy outlives its source.
    for name in ("source_id", "source_criterion_id", "source_transcript_revision_id"):
        assert not table.c[name].foreign_keys, name
    assert "HOM-135" in ActionDeliveryRequest.__doc__
    assert "immutable" in ActionDeliveryRequest.__doc__


def _list(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{value}'" for value in values) + ")"


def test_the_frozen_lists_and_the_check_strings():
    assert ACTION_PROPOSAL_TYPES == ("crm_update", "ticket_create", "coaching_task")
    assert ACTION_PROPOSAL_SOURCES == ("action_item", "review_finding")
    assert ACTION_PROPOSAL_STATUSES == ("pending", "approved", "rejected", "revoked")
    assert ACTION_PROPOSAL_OPEN_STATUSES == ("pending", "approved")
    assert ACTION_PROPOSAL_REJECT_REASONS == ("not_needed", "incorrect", "duplicate", "other")
    assert ACTION_PROPOSAL_REVOKE_REASONS == ("approved_in_error", "no_longer_needed", "other")
    for name, values in (
        ("ck_action_proposals_action_type", ACTION_PROPOSAL_TYPES),
        ("ck_action_proposals_source_type", ACTION_PROPOSAL_SOURCES),
        ("ck_action_proposals_status", ACTION_PROPOSAL_STATUSES),
    ):
        assert PROPOSAL_CHECKS[name].endswith(f"IN {_list(values)}"), name
    assert OPEN_ITEM_WHERE.startswith(f"status IN {_list(ACTION_PROPOSAL_OPEN_STATUSES)}")
    for check in (*PROPOSAL_CHECKS.values(), *REQUEST_CHECKS.values()):
        lowered = check.lower()
        assert "~" not in lowered and "regexp" not in lowered and "similar" not in lowered
        # No user column: those are SET NULL on a user's removal; a check on them would
        # make the removal fail.
        for column in USER_COLUMNS:
            assert column not in check, column


def test_the_tables_are_workspace_tables_and_the_requests_take_no_update():
    for table in TABLES:
        assert table in WORKSPACE_TABLES, table
        assert table not in APPEND_ONLY_TABLES, table
        assert table not in NO_DELETE_TABLES, table
        assert table not in SHARED_CONTENT_TABLES, table  # never through a share token
        assert table not in UNPROTECTED_TABLES, table
        assert POLICIES[table] == (f"workspace_id = {TENANT}", f"workspace_id = {TENANT}")
    # PR 2 decides and revokes the proposals; an approved request never changes.
    assert PROPOSALS not in NO_UPDATE_TABLES
    assert REQUESTS in NO_UPDATE_TABLES
    # Next to the coaching tasks, the proposals first.
    assert WORKSPACE_TABLES.index("coaching_tasks") < WORKSPACE_TABLES.index(PROPOSALS)
    assert WORKSPACE_TABLES.index(PROPOSALS) < WORKSPACE_TABLES.index(REQUESTS)


# --- cascades, removals and user removal ----------------------------------------------------


def _with_request(tenant: dict, key: str) -> dict:
    """An approved CRM update with its request, and a pending coaching task."""
    setup = _approved_on(tenant, key)
    request_id = _add(ActionDeliveryRequest, _request(setup, setup["proposal_id"]))
    coaching_id = _add(ActionProposal, _coaching(setup))
    return setup | {"request_id": request_id, "coaching_id": coaching_id}


def test_a_conversation_a_workspace_a_proposal_or_a_revision_takes_the_rows(client, email_outbox):
    first = _tenant(client, email_outbox, "cascade-a")
    second = _tenant(client, email_outbox, "cascade-b")
    by_meeting = _with_request(second, "cascade_meeting")
    by_proposal = _with_request(second, "cascade_proposal")
    by_revision = _with_request(second, "cascade_revision")
    kept = _with_request(second, "cascade_kept")
    elsewhere = _with_request(first, "cascade_elsewhere")

    # Removals by the owner (DBA here): the FK actions run as the table owner.
    with inspection_session_factory()() as db:
        db.execute(delete(Meeting).where(Meeting.id == by_meeting["meeting_id"]))
        db.execute(delete(ActionProposal).where(ActionProposal.id == by_proposal["proposal_id"]))
        db.execute(
            delete(TranscriptRevision).where(TranscriptRevision.id == by_revision["revision_id"])
        )
        db.commit()
    # The meeting takes both proposals and the request; the proposal takes its request
    # only; the revision takes both proposals and the request.
    assert _ids(REQUESTS) == {kept["request_id"], elsewhere["request_id"]}
    assert _ids(PROPOSALS) == {
        by_proposal["coaching_id"],
        kept["proposal_id"],
        kept["coaching_id"],
        elsewhere["proposal_id"],
        elsewhere["coaching_id"],
    }

    # The workspace takes its rows, its external records included.
    with inspection_session_factory()() as db:
        db.execute(delete(Workspace).where(Workspace.id == first["workspace_id"]))
        db.commit()
    assert _ids(REQUESTS) == {kept["request_id"]}
    assert _ids(PROPOSALS) == {by_proposal["coaching_id"], kept["proposal_id"], kept["coaching_id"]}


def test_a_removed_source_leaves_the_proposal_with_a_null_id(client, email_outbox):
    tenant = _tenant(client, email_outbox, "removed-source")
    setup = _with_request(tenant, "removed_source")
    with inspection_session_factory()() as db:
        db.execute(delete(ActionItem).where(ActionItem.id == setup["action_item_id"]))
        db.execute(delete(ReviewItem).where(ReviewItem.id == setup["review_item_id"]))
        db.commit()
    with inspection_session_factory()() as db:
        crm = db.get(ActionProposal, setup["proposal_id"])
        coaching = db.get(ActionProposal, setup["coaching_id"])
        request = db.get(ActionDeliveryRequest, setup["request_id"])
        assert crm.action_item_id is None and crm.status == "approved"
        assert coaching.review_item_id is None and coaching.criterion_id == CRITERION
        # The request is a copy: its source id stays as it was at approval.
        assert request.source_id == setup["action_item_id"]


def test_a_named_external_record_cannot_be_deleted(client, email_outbox):
    tenant = _tenant(client, email_outbox, "restrict")
    # A pending CRM update names its record; no request exists.
    setup = _source(tenant, "restrict")
    _add(ActionProposal, _crm(setup))
    # A request names a record that its proposal does not (PR 2 copies, so this only
    # shows the request's own key).
    request_only = _add(ExternalRecord, _record(setup, "C-request-only"))
    other = _approved_on(tenant, "restrict_other")
    _add(
        ActionDeliveryRequest,
        _request(other, other["proposal_id"], target_external_record_id=request_only),
    )
    for record_id, key in (
        (setup["record_id"], f"{PROPOSALS}_target_external_record_id_fkey"),
        (request_only, f"{REQUESTS}_target_external_record_id_fkey"),
    ):
        pattern = key if _on_postgres() else "FOREIGN KEY constraint failed"
        with pytest.raises(IntegrityError, match=pattern):
            with inspection_session_factory()() as db:
                db.execute(delete(ExternalRecord).where(ExternalRecord.id == record_id))
                db.commit()
    # Allow-control: a record no row names goes.
    unused = _add(ExternalRecord, _record(setup, "C-unused"))
    with inspection_session_factory()() as db:
        db.execute(delete(ExternalRecord).where(ExternalRecord.id == unused))
        db.commit()
    assert _ids("external_records", id=unused) == set()


def test_deleting_a_user_clears_every_user_column_and_keeps_the_rows(client, email_outbox):
    tenant = _tenant(client, email_outbox, "user-removal")
    with inspection_session_factory()() as db:
        user = User(email="proposals-removed@example.com", display_name="Removed")
        db.add(user)
        db.commit()
        user_id = user.id
    setup = _source(tenant, "user_removal")
    values = _revoked(_crm, setup)
    values.update({column: user_id for column in PROPOSAL_USER_COLUMNS})
    proposal_id = _add(ActionProposal, values)
    request_id = _add(
        ActionDeliveryRequest, _request(setup, proposal_id, approved_by_user_id=user_id)
    )

    with inspection_session_factory()() as db:
        db.execute(delete(User).where(User.id == user_id))
        db.commit()
    with inspection_session_factory()() as db:
        proposal = db.get(ActionProposal, proposal_id)
        for column in PROPOSAL_USER_COLUMNS:
            assert getattr(proposal, column) is None, column
        assert (proposal.status, proposal.revoke_reason) == ("revoked", "no_longer_needed")
        request = db.get(ActionDeliveryRequest, request_id)
        assert request.approved_by_user_id is None
        assert request.payload_hash == PAYLOAD_HASH


# --- the migration (SQLite) -----------------------------------------------------------------


def _migration_module():
    return importlib.import_module(f"backend.migrations.versions.{NEW_REVISION}_action_proposals")


def remove_action_proposals(connection) -> None:
    """Take a ``create_all`` schema back to before HOM-135.

    Older migration tests build the full ORM, drop the newer tables and stamp an
    older revision.  The requests go first: they name the proposals.
    """
    for name in reversed(TABLES):
        Base.metadata.tables[name].drop(connection)


def test_the_migration_follows_the_previous_head():
    module = _migration_module()
    assert module.revision == NEW_REVISION
    assert module.down_revision == BASE_REVISION
    doc = module.__doc__
    assert doc.splitlines()[0] == (
        "Action proposals and approved delivery requests, schema only (HOM-135, WP24-T03)"
    )
    for words in ("Why", "No data changes", "Downgrade", "NO_UPDATE_TABLES"):
        assert words in doc, words
    # Frozen copies of the model's check strings.
    assert module.PROPOSAL_CHECKS == PROPOSAL_CHECKS
    assert module.REQUEST_CHECKS == REQUEST_CHECKS
    assert str(module.OPEN_ITEM_WHERE) == OPEN_ITEM_WHERE
    assert str(module.OPEN_FINDING_WHERE) == OPEN_FINDING_WHERE


def test_the_code_has_one_migration_head_this_revision():
    from backend.observability.release import code_migration_head

    assert code_migration_head() == HEAD_REVISION


def test_alembic_heads_prints_exactly_this_revision():
    import io

    from alembic import command
    from alembic.config import Config

    from backend.observability.release import PROJECT_ROOT

    shown = io.StringIO()
    command.heads(Config(str(PROJECT_ROOT / "alembic.ini"), stdout=shown))
    lines = [line for line in shown.getvalue().splitlines() if line.strip()]
    assert lines == [f"{HEAD_REVISION} (head)"], shown.getvalue()


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
    """Every table and index of the database, as (type, name, table)."""
    return {
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT type, name, tbl_name FROM sqlite_master WHERE tbl_name <> 'alembic_version'"
            )
        ).all()
    }


def _partial_sql(connection) -> dict[str, str]:
    """The SQL text SQLite keeps for the two partial unique indexes."""
    return {
        name: connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = :n"), {"n": name}
        ).scalar_one()
        for name in ("uq_action_proposals_open_item", "uq_action_proposals_open_finding")
    }


def _all_shapes(connection) -> dict:
    return {name: _reflect(connection, name) for name in inspect(connection).get_table_names()}


def test_on_sqlite_the_migration_builds_the_orm_tables_and_drops_only_them():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            orm = {table: _reflect(connection, table) for table in TABLES}
            orm_partial = _partial_sql(connection)
            remove_action_proposals(connection)
            before = _all_shapes(connection)
            before_objects = _objects(connection)

            module = _migration_module()
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
            assert {table: _reflect(connection, table) for table in TABLES} == orm
            assert sorted(name for name, _ in orm[PROPOSALS]["checks"]) == sorted(PROPOSAL_CHECKS)
            assert sorted(name for name, _ in orm[REQUESTS]["checks"]) == sorted(REQUEST_CHECKS)
            # The partial unique indexes keep the same WHERE clauses as the ORM's.
            assert _partial_sql(connection) == orm_partial
            for name, where in (
                ("uq_action_proposals_open_item", OPEN_ITEM_WHERE),
                ("uq_action_proposals_open_finding", OPEN_FINDING_WHERE),
            ):
                assert orm_partial[name].endswith(f"WHERE {where}"), name
            # No other table changed.
            after = _all_shapes(connection)
            assert {k: v for k, v in after.items() if k not in TABLES} == before

            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
            assert _all_shapes(connection) == before
            assert _objects(connection) == before_objects
    finally:
        engine.dispose()


def _alembic_at(monkeypatch, url: str):
    from alembic.config import Config

    from backend.config import reset_settings_cache
    from backend.observability.release import PROJECT_ROOT

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def test_on_sqlite_alembic_upgrades_to_the_head_checks_clean_and_downgrades_one_step(
    tmp_path, monkeypatch
):
    from alembic import command
    from alembic.util.exc import AutogenerateDiffsDetected

    from backend.config import reset_settings_cache

    url = f"sqlite:///{tmp_path / 'action-proposals.db'}"
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            orm = {table: _reflect(connection, table) for table in TABLES}
            remove_action_proposals(connection)
            before_objects = _objects(connection)

        config = _alembic_at(monkeypatch, url)
        command.stamp(config, BASE_REVISION)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            assert version.scalar() == HEAD_REVISION
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


# --- PostgreSQL: the schema, the migration, isolation and grants ----------------------------


def _shape(table: str) -> dict:
    """The table as the catalog shows it: columns, constraints, indexes, policy, grants."""
    with inspection_session_factory()() as db:
        columns = db.execute(
            text(
                "SELECT column_name, data_type, character_maximum_length, is_nullable, "
                "column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table},
        ).all()
        constraints = db.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
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
        "constraints": dict(constraints),
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
    """Every other table of the schema, so a test can prove it did not change."""
    excluded = {"a": PROPOSALS, "b": REQUESTS}
    with inspection_session_factory()() as db:
        return {
            name: sorted(tuple(row) for row in db.execute(text(sql), excluded).all())
            for name, sql in OTHER_TABLE_QUERIES.items()
        }


def _varchar(length: int, nullable: str = "NO") -> tuple:
    return ("character varying", length, nullable, None)


def _plain(kind: str, nullable: str = "NO") -> tuple:
    return (kind, None, nullable, None)


def _timestamp(nullable: str = "NO") -> tuple:
    return _plain("timestamp with time zone", nullable)


EXPECTED_COLUMNS = {
    PROPOSALS: {
        "id": _varchar(36),
        "workspace_id": _varchar(36),
        "meeting_id": _varchar(36),
        "action_type": _varchar(20),
        "source_type": _varchar(20),
        "action_item_id": _varchar(36, "YES"),
        "review_item_id": _varchar(36, "YES"),
        "criterion_id": _varchar(64, "YES"),
        "source_transcript_revision_id": _varchar(36),
        "source_digest": _varchar(64),
        "target_system": _varchar(60, "YES"),
        "target_external_record_id": _varchar(36, "YES"),
        "payload_json": _plain("json"),
        "payload_hash": _varchar(64),
        "payload_version": _plain("integer"),
        "status": _varchar(20),
        "revision": _plain("integer"),
        "created_by_user_id": _varchar(36, "YES"),
        "decided_by_user_id": _varchar(36, "YES"),
        "revoked_by_user_id": _varchar(36, "YES"),
        "decided_at": _timestamp("YES"),
        "revoked_at": _timestamp("YES"),
        "reject_reason": _varchar(30, "YES"),
        "revoke_reason": _varchar(30, "YES"),
        "created_at": _timestamp(),
        "updated_at": _timestamp(),
    },
    REQUESTS: {
        "id": _varchar(36),
        "workspace_id": _varchar(36),
        "meeting_id": _varchar(36),
        "proposal_id": _varchar(36),
        "action_type": _varchar(20),
        "target_system": _varchar(60, "YES"),
        "target_external_record_id": _varchar(36, "YES"),
        "payload_json": _plain("json"),
        "payload_hash": _varchar(64),
        "payload_version": _plain("integer"),
        "source_type": _varchar(20),
        "source_id": _varchar(36),
        "source_criterion_id": _varchar(64, "YES"),
        "source_transcript_revision_id": _varchar(36),
        "source_digest": _varchar(64),
        "idempotency_key": _varchar(64),
        "approved_by_user_id": _varchar(36, "YES"),
        "approved_at": _timestamp(),
        "created_at": _timestamp(),
    },
}


def _foreign_key(table: str, column: str, target: str, action: str) -> tuple[str, str]:
    return (
        f"{table}_{column}_fkey",
        f"FOREIGN KEY ({column}) REFERENCES {target}(id) ON DELETE {action}",
    )


EXPECTED_CONSTRAINTS = {
    PROPOSALS: dict(
        [
            (f"{PROPOSALS}_pkey", "PRIMARY KEY (id)"),
            _foreign_key(PROPOSALS, "workspace_id", "workspaces", "CASCADE"),
            _foreign_key(PROPOSALS, "meeting_id", "meetings", "CASCADE"),
            _foreign_key(PROPOSALS, "action_item_id", "action_items", "SET NULL"),
            _foreign_key(PROPOSALS, "review_item_id", "review_items", "SET NULL"),
            _foreign_key(
                PROPOSALS, "source_transcript_revision_id", "transcript_revisions", "CASCADE"
            ),
            _foreign_key(PROPOSALS, "target_external_record_id", "external_records", "RESTRICT"),
            *(
                _foreign_key(PROPOSALS, column, "users", "SET NULL")
                for column in PROPOSAL_USER_COLUMNS
            ),
            ("uq_action_proposals_scope", "UNIQUE (id, workspace_id, meeting_id)"),
        ]
    ),
    REQUESTS: dict(
        [
            (f"{REQUESTS}_pkey", "PRIMARY KEY (id)"),
            _foreign_key(REQUESTS, "workspace_id", "workspaces", "CASCADE"),
            _foreign_key(REQUESTS, "meeting_id", "meetings", "CASCADE"),
            _foreign_key(REQUESTS, "target_external_record_id", "external_records", "RESTRICT"),
            _foreign_key(REQUESTS, "approved_by_user_id", "users", "SET NULL"),
            (
                "fk_action_delivery_requests_proposal",
                "FOREIGN KEY (proposal_id, workspace_id, meeting_id) REFERENCES "
                "action_proposals(id, workspace_id, meeting_id) ON DELETE CASCADE",
            ),
            ("uq_action_delivery_requests_proposal", "UNIQUE (proposal_id)"),
            ("uq_action_delivery_requests_idempotency_key", "UNIQUE (idempotency_key)"),
            ("uq_action_delivery_requests_scope", "UNIQUE (id, workspace_id, meeting_id)"),
        ]
    ),
}


def _index(table: str, name: str, columns: str, *, unique: bool = False) -> tuple[str, str]:
    kind = "UNIQUE INDEX" if unique else "INDEX"
    return name, f"CREATE {kind} {name} ON public.{table} USING btree ({columns})"


EXPECTED_INDEXES = {
    PROPOSALS: dict(
        _index(PROPOSALS, name, columns, unique=unique)
        for name, columns, unique in (
            (f"{PROPOSALS}_pkey", "id", True),
            ("uq_action_proposals_scope", "id, workspace_id, meeting_id", True),
            ("ix_action_proposals_workspace_id", "workspace_id", False),
            ("ix_action_proposals_action_item_id", "action_item_id", False),
            ("ix_action_proposals_review_item_id", "review_item_id", False),
            (
                "ix_action_proposals_source_transcript_revision_id",
                "source_transcript_revision_id",
                False,
            ),
            ("ix_action_proposals_target_external_record_id", "target_external_record_id", False),
            ("ix_action_proposals_queue", "workspace_id, status, created_at, id", False),
            ("ix_action_proposals_workspace_meeting", "workspace_id, meeting_id", False),
        )
    ),
    REQUESTS: dict(
        _index(REQUESTS, name, columns, unique=unique)
        for name, columns, unique in (
            (f"{REQUESTS}_pkey", "id", True),
            ("uq_action_delivery_requests_proposal", "proposal_id", True),
            ("uq_action_delivery_requests_idempotency_key", "idempotency_key", True),
            ("uq_action_delivery_requests_scope", "id, workspace_id, meeting_id", True),
            ("ix_action_delivery_requests_workspace_id", "workspace_id", False),
            (
                "ix_action_delivery_requests_target_external_record_id",
                "target_external_record_id",
                False,
            ),
            ("ix_action_delivery_requests_workspace_meeting", "workspace_id, meeting_id", False),
            ("ix_action_delivery_requests_approved", "workspace_id, approved_at, id", False),
        )
    ),
}
EXPECTED_GRANTS = {
    PROPOSALS: ["DELETE", "INSERT", "SELECT", "UPDATE"],
    REQUESTS: ["DELETE", "INSERT", "SELECT"],
}
EXPECTED_CHECKS = {PROPOSALS: PROPOSAL_CHECKS, REQUESTS: REQUEST_CHECKS}


def _assert_head_shape(table: str) -> dict:
    shape = _shape(table)
    assert shape["columns"] == EXPECTED_COLUMNS[table], table
    constraints = dict(shape["constraints"])
    named_checks = {name for name in constraints if name.startswith("ck_")}
    assert named_checks == set(EXPECTED_CHECKS[table])  # their behaviour is pinned by RULES
    for name in named_checks:
        constraints.pop(name)
    assert constraints == EXPECTED_CONSTRAINTS[table], table
    indexes = dict(shape["indexes"])
    if table == PROPOSALS:
        # PostgreSQL rewrites the IN list, so only the parts of the partial indexes are
        # pinned.  Only the open statuses are in them.
        for name, columns, column in (
            ("uq_action_proposals_open_item", "action_item_id, action_type", "action_item_id"),
            ("uq_action_proposals_open_finding", "review_item_id, criterion_id", "review_item_id"),
        ):
            head, where = indexes.pop(name).split(" WHERE ")
            assert head == _index(table, name, columns, unique=True)[1], name
            assert "'pending'" in where and "'approved'" in where, name
            assert "'rejected'" not in where and "'revoked'" not in where, name
            assert f"{column} IS NOT NULL" in where, name
    assert indexes == EXPECTED_INDEXES[table], table
    # The tenant policy only: no share-token or service clause, the same check on write.
    ((name, policy),) = shape["policies"].items()
    permissive, roles, command, using, check = policy
    assert (name, permissive, roles, command) == (POLICY_NAME, "PERMISSIVE", "{public}", "ALL")
    assert using == check
    assert "homayar.workspace_id" in using
    assert "share" not in using and "service" not in using
    assert shape["rls"] == [(True, True)]
    assert shape["grants"] == EXPECTED_GRANTS[table], table
    return shape


@pytest.mark.postgres
def test_the_orm_schema_has_the_tables(client):
    _requires_postgres()
    for table in TABLES:
        _assert_head_shape(table)


@pytest.mark.postgres
def test_the_migration_creates_and_drops_only_the_two_tables(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    from alembic import command

    from backend.config import reset_settings_cache

    tenant = _tenant(client, email_outbox, "migration")
    orm_shapes = {table: _assert_head_shape(table) for table in TABLES}
    _with_request(tenant, "migration")
    counts = {
        table: _scalar(f"SELECT count(*) FROM {table}")  # noqa: S608
        for table in ("action_items", "review_items", "external_records", "meetings")
    }

    config = _alembic_at(monkeypatch, settings.database_url)
    try:
        command.stamp(config, "head")
        head_others = _other_tables()
        command.downgrade(config, BASE_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        for table in TABLES:
            assert _scalar("SELECT to_regclass(:t) IS NULL", t=f"public.{table}"), table
            assert _shape(table) == EMPTY_SHAPE, table
        # Only the two tables are lost: every other table, its rows included, stays.
        assert _other_tables() == head_others
        for table, count in counts.items():
            assert _scalar(f"SELECT count(*) FROM {table}") == count, table  # noqa: S608

        command.upgrade(config, NEW_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == NEW_REVISION
        assert {table: _shape(table) for table in TABLES} == orm_shapes
        assert all(_ids(table) == set() for table in TABLES)
        assert _other_tables() == head_others

        # The migrated schema refuses the same deny cases and takes the allow cases.
        for rules in RULES:
            rules(tenant)

        # Down and up again with rows present.
        command.downgrade(config, BASE_REVISION)
        assert _other_tables() == head_others
        command.upgrade(config, "head")
        assert {table: _shape(table) for table in TABLES} == orm_shapes
        assert all(_ids(table) == set() for table in TABLES)
    finally:
        reset_settings_cache()


def _write_rows(db, setup: dict) -> dict:
    """An approved proposal and its request, written in the session's transaction."""
    proposal = ActionProposal(**_approved(_crm, setup))
    db.add(proposal)
    db.flush()
    request = ActionDeliveryRequest(**_request(setup, proposal.id))
    db.add(request)
    db.flush()
    return setup | {"proposal_id": proposal.id, "request_id": request.id}


@pytest.mark.postgres
def test_a_session_reads_and_writes_only_its_own_workspace(client, email_outbox):
    _requires_postgres()
    first = _source(_tenant(client, email_outbox, "tenant-a"), "tenant_a")
    second = _source(_tenant(client, email_outbox, "tenant-b"), "tenant_b")
    factory = get_session_factory()

    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        first = _write_rows(db, first)
        db.commit()

    # Bound to B: none of A's rows; B writes its own.
    with factory() as db:
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        for table in TABLES:
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0  # noqa: S608
        second = _write_rows(db, second)
        db.commit()

    # Bound to A: only A's rows; an insert with B's ids fails (WITH CHECK).
    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        for table in TABLES:
            visible = db.execute(text(f"SELECT workspace_id FROM {table}")).scalars()  # noqa: S608
            assert list(visible) == [first["workspace_id"]], table
    for model, values in (
        (ActionProposal, _crm(second, action_item_id=None)),
        (ActionDeliveryRequest, _request(second, second["proposal_id"], idempotency_key="e" * 64)),
    ):
        with factory() as db:
            bind_tenant_context(db, workspace_id=first["workspace_id"])
            db.add(model(**values))
            with pytest.raises(DBAPIError, match="row-level security"):
                db.flush()
            db.rollback()

    # No workspace bound: nothing.
    with factory() as db:
        for table in TABLES:
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0  # noqa: S608


@pytest.mark.postgres
def test_a_share_token_does_not_reach_the_rows(client, email_outbox):
    _requires_postgres()
    setup = _with_request(_tenant(client, email_outbox, "share"), "share")
    token_hash = "cd" * 32
    with inspection_session_factory()() as db:
        db.add(
            ShareLink(
                workspace_id=setup["workspace_id"],
                meeting_id=setup["meeting_id"],
                created_by_user_id=setup["user_id"],
                token_hash=token_hash,
                audience=ShareAudience.LINK,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        db.commit()
    with get_session_factory()() as db:
        bind_capability_context(db, share_token_hash=token_hash)
        # Control: the shared conversation itself is readable through the token.
        assert db.scalars(select(Meeting.id)).all() == [setup["meeting_id"]]
        assert db.scalars(select(ActionProposal.id)).all() == []
        assert db.scalars(select(ActionDeliveryRequest.id)).all() == []
        db.rollback()


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


@pytest.mark.postgres
def test_the_app_role_may_not_update_a_request(client, email_outbox):
    _requires_postgres()
    setup = _source(_tenant(client, email_outbox, "grants"), "grants")
    full = {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": True}
    no_update = {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": True}
    assert _privileges(PROPOSALS) == full
    assert _privileges(REQUESTS) == no_update
    _apply_as_owner()  # idempotent: the revoke stays
    assert _privileges(PROPOSALS) == full
    assert _privileges(REQUESTS) == no_update

    factory = get_session_factory()
    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=setup["workspace_id"])
        setup = _write_rows(db, setup)  # the app role INSERTs both
        db.commit()

    # The app role may not UPDATE a request, not even its own workspace's.
    with factory() as db:
        bind_tenant_context(db, workspace_id=setup["workspace_id"])
        with pytest.raises(DBAPIError, match=f"permission denied for table {REQUESTS}"):
            db.execute(
                text(f"UPDATE {REQUESTS} SET payload_version = 2 WHERE id = :id"),  # noqa: S608
                {"id": setup["request_id"]},
            )
        db.rollback()

    # Allow-control: the compare-and-set of PR 2 on the proposal (a revoke).
    with factory() as db:
        bind_tenant_context(db, workspace_id=setup["workspace_id"])
        updated = db.execute(
            text(
                f"UPDATE {PROPOSALS} SET status = 'revoked', revoked_at = :now, "  # noqa: S608
                "revoke_reason = 'approved_in_error', revision = revision + 1 "
                "WHERE id = :id AND workspace_id = :w AND revision = 1 AND status = 'approved'"
            ),
            {"now": NOW, "id": setup["proposal_id"], "w": setup["workspace_id"]},
        )
        assert updated.rowcount == 1
        db.commit()

    # The app role DELETEs a request and a proposal.
    with factory() as db:
        bind_tenant_context(db, workspace_id=setup["workspace_id"])
        for table, row_id in ((REQUESTS, setup["request_id"]), (PROPOSALS, setup["proposal_id"])):
            deleted = db.execute(
                text(f"DELETE FROM {table} WHERE id = :id"),  # noqa: S608
                {"id": row_id},
            )
            assert deleted.rowcount == 1, table
        db.commit()
    for table in TABLES:
        assert _ids(table) == set(), table


# --- the conversation purge and the backup reconciliation -----------------------------------


def test_the_purge_deletes_both_tables_first_and_counts_them(client, email_outbox):
    tenant = _tenant(client, email_outbox, "purge-order")
    setup = _with_request(tenant, "purge_ordered")
    plain = _source(tenant, "purge_plain")
    kept = _with_request(tenant, "purge_kept")
    statements: list[str] = []

    def record(_connection, _cursor, statement, *_args) -> None:
        statements.append(statement)

    factory = get_session_factory()
    removed = {}
    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            for key, source in (("ordered", setup), ("plain", plain)):
                meeting = db.get(Meeting, source["meeting_id"])
                removed[key] = _remove_content(
                    db, workspace_id=tenant["workspace_id"], meeting=meeting
                )
            db.commit()
        finally:
            event.remove(engine, "before_cursor_execute", record)

    deletes = [s.split()[2] for s in statements if s.lstrip().upper().startswith("DELETE FROM")]
    # The requests before the proposals, and both before the rows whose cascades would
    # remove them uncounted.
    assert deletes.index(REQUESTS) < deletes.index(PROPOSALS)
    for table in ("action_items", "review_items", "transcript_revisions"):
        assert deletes.index(PROPOSALS) < deletes.index(table), table
    # The same conversation without proposals: two proposals and one request more.
    assert removed["ordered"] == removed["plain"] + 3
    for table in TABLES:
        assert _ids(table, meeting_id=setup["meeting_id"]) == set(), table
    assert len(_ids(REQUESTS, meeting_id=kept["meeting_id"])) == 1
    assert len(_ids(PROPOSALS, meeting_id=kept["meeting_id"])) == 2


def _purgeable(tenant: dict, key: str, meeting_id: str) -> dict:
    """A processed conversation with two proposals and one request on it."""
    setup = _source(tenant, key, meeting_id=meeting_id)
    proposal_id = _add(ActionProposal, _approved(_crm, setup))
    _add(ActionDeliveryRequest, _request(setup, proposal_id))
    _add(ActionProposal, _coaching(setup))
    return setup


def test_the_conversation_purge_removes_the_rows_of_its_conversation_only(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _ledger_tenant(client, email_outbox, "proposals-purge")
    queued, kept, plain = [
        _processed(client, tenant, settings, key=f"proposals-purge-{index:04d}", index=index)
        for index in range(3)
    ]
    for meeting_id, key in ((queued, "proposals_purged"), (kept, "proposals_kept")):
        _purgeable(tenant, key, meeting_id)
    assert len(_ids(PROPOSALS, meeting_id=queued)) == 2

    for meeting_id in (queued, plain):
        _delete_conversation(client, tenant, meeting_id)
        assert _purge(meeting_id).state is PurgeState.COMPLETED
    for table in TABLES:
        assert _ids(table, meeting_id=queued) == set(), table
    # Another conversation of the workspace keeps its rows.
    assert len(_ids(PROPOSALS, meeting_id=kept)) == 2
    assert len(_ids(REQUESTS, meeting_id=kept)) == 1
    # The purge deletes and counts the two proposals, the request, the review item, the
    # scorecard result and the action item itself, before any cascade could.
    assert _purge(queued).content_rows_removed == _purge(plain).content_rows_removed + 6


def test_a_held_conversation_keeps_its_proposals_until_the_release(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _ledger_tenant(client, email_outbox, "proposals-hold")
    meeting_id = _processed(client, tenant, settings, key="proposals-hold-0001")
    _purgeable(tenant, "proposals_held", meeting_id)
    held = client.post(
        f"/api/v1/meetings/{meeting_id}/legal-hold",
        json={"reason": "litigation 2026-11", "review_at": _review(30)},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert held.status_code == 200, held.text

    # The deletion of a held conversation: the purge waits and the rows stay.
    _delete_conversation(client, tenant, meeting_id)
    purge = _purge(meeting_id)
    assert purge.state is PurgeState.PENDING and purge.last_error == ERROR_LEGAL_HOLD
    assert len(_ids(PROPOSALS, meeting_id=meeting_id)) == 2
    assert len(_ids(REQUESTS, meeting_id=meeting_id)) == 1
    deferred = _sweep(settings, tenant["workspace_id"], now=datetime.now(UTC) + timedelta(days=1))
    assert deferred["purges_deferred_hold"] == 1, deferred
    assert len(_ids(PROPOSALS, meeting_id=meeting_id)) == 2

    # After the release, the next attempt removes them.
    for media in _media(meeting_id):
        released = client.post(
            f"/api/v1/media/{media.id}/legal-hold/release",
            json={"reason": "matter closed"},
            headers=_headers(tenant),
            cookies=tenant["cookies"],
        )
        assert released.status_code == 200, released.text
    final = _sweep(settings, tenant["workspace_id"], now=datetime.now(UTC) + timedelta(seconds=1))
    assert final["purges_completed"] == 1, final
    for table in TABLES:
        assert _ids(table, meeting_id=meeting_id) == set(), table


def test_the_backup_reconciliation_counts_both_tables():
    for table in TABLES:
        assert table in RECONCILED_TABLES, table
        columns = Base.metadata.tables[table].c
        assert "meeting_id" in columns and "workspace_id" in columns
    # Next to the coaching tasks.
    assert RECONCILED_TABLES.index("coaching_tasks") < RECONCILED_TABLES.index(PROPOSALS)
    assert RECONCILED_TABLES.index(PROPOSALS) < RECONCILED_TABLES.index(REQUESTS)


# --- the ADR --------------------------------------------------------------------------------


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


def test_the_adr_is_named_and_built_like_the_others():
    content = _adr()
    assert content.startswith("# ADR 0082: ")
    assert "Proposed (HOM-135, WP24-T03, F60)" in content
    assert "2026-11-14" in content
    for number in ("0005", "0021", "0048", "0062", "0065"):
        assert f"ADR {number}" in content, number
    for heading in ("Context", "Decision", "Consequences", "Alternatives", "Not built"):
        assert re.search(rf"^## {heading}", content, re.MULTILINE), heading
    for section in ("D1", "D2", "D3", "D4", "D5", "D6", "D7"):
        assert re.search(rf"^### {section}\.", content, re.MULTILINE), section


def test_the_adr_uses_no_em_dash_and_no_double_hyphen_dash():
    content = _adr()
    assert "—" not in content
    assert not re.search(r"(^|\s)--(\s|$)", content, re.MULTILINE)


# What the SPEC says the ADR records; any one spelling of a topic is enough.
ADR_TOPICS = {
    "proposals": (PROPOSALS,),
    "requests": (REQUESTS,),
    "statuses": ("`revoked`",),
    "pins": ("source_transcript_revision_id",),
    "digest": ("source_digest",),
    "payload_hash": ("payload_hash",),
    "payload_version": ("payload_version",),
    "no_update": ("NO_UPDATE_TABLES",),
    "revoke_on_proposal": ("revoke lives on the proposal", "Revoke lives on the proposal"),
    "hom136_lock": ("FOR UPDATE",),
    "hom136_started": ("delivery started",),
    "idempotency": ("idempotency_key",),
    "routes": ("/api/v1/action-proposals",),
    "module": ('require_module("integrations")',),
    "session_only": ("API keys", "API key"),
    "propose": ("action:propose",),
    "approve": ("action:approve",),
    "expected_revision": ("expected_revision",),
    "stale": ("stale",),
    "conflict": ("409",),
    "refresh": ("refresh",),
    "audit_ids": ("never payload text", "never the payload text"),
    "self_approval": ("self-approval", "Self-approval"),
    "scope_rule": ("own workspace and meeting",),
    "rls": ("row-level security",),
    "purge": ("purge",),
    "backup": ("reconcil",),
}


@pytest.mark.parametrize("spellings", ADR_TOPICS.values(), ids=ADR_TOPICS.keys())
def test_the_adr_records_the_design(spellings):
    content = _adr()
    assert any(spelling in content for spelling in spellings), spellings
