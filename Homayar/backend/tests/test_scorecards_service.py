"""HOM-54: publishing and reading scorecard versions (``services/scorecard_service.py``).

Every publish appends the next version of one key in one workspace, after the caller names
the version it read.  A stale version, also one found by the unique constraint after a
concurrent insert, is a conflict that writes nothing.  Reads are per workspace.  The
sessions are the application's own (``homayar_app`` under row-level security on
PostgreSQL), bound to the tenant like a request.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from backend.database.relational import get_session_factory
from backend.database.saas_models import ScorecardVersion
from backend.database.tenant_context import bind_tenant_context
from backend.scorecards import SCORECARDS_VERSION, ScorecardError, definition_digest
from backend.services import scorecard_service
from backend.services.scorecard_service import (
    ScorecardVersionConflict,
    get_version,
    latest_versions,
    list_versions,
    publish_version,
)
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user

NOW = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)


def _definition(name: str = "Inbound sales", threshold: int = 80) -> dict:
    return {
        "name": name,
        "pass_threshold": threshold,
        "criteria": [
            {
                "criterion_id": "greeting",
                "label": "Greets the customer",
                "weight": 100,
                "check": {
                    "check_id": "greeting",
                    "version": 1,
                    "kind": "required_phrase",
                    "phrases": ["hello"],
                },
            }
        ],
    }


def _tenant(client, email_outbox, label: str) -> dict:
    credentials = register_user(client, email_outbox, email=f"scorecard-svc-{label}@example.com")
    session = login_user(client, credentials)
    return {"user_id": session["user"]["id"], "workspace_id": session["workspaces"][0]["id"]}


def _session(tenant: dict):
    db = get_session_factory()()
    bind_tenant_context(db, workspace_id=tenant["workspace_id"])
    return db


def _publish(tenant: dict, key: str = "inbound_sales", expected: int = 0, **extra):
    arguments = {
        "workspace_id": tenant["workspace_id"],
        "scorecard_key": key,
        "definition": _definition(),
        "expected_version": expected,
        "reason": "first publish",
        "actor_user_id": tenant["user_id"],
        "now": NOW,
        **extra,
    }
    with _session(tenant) as db:
        row = publish_version(db, **arguments)
        db.commit()
        return {
            "id": row.id,
            "version": row.version,
            "scorecard_key": row.scorecard_key,
            "document": row.document,
            "digest": row.digest,
            "reason": row.reason,
        }


def _all_rows() -> list[tuple]:
    with inspection_session_factory()() as db:
        return [
            tuple(row)
            for row in db.execute(
                select(
                    ScorecardVersion.workspace_id,
                    ScorecardVersion.scorecard_key,
                    ScorecardVersion.version,
                    ScorecardVersion.reason,
                    ScorecardVersion.digest,
                ).order_by(
                    ScorecardVersion.workspace_id,
                    ScorecardVersion.scorecard_key,
                    ScorecardVersion.version,
                )
            ).all()
        ]


def test_the_first_publish_stores_version_1_with_the_validated_document(client, email_outbox):
    tenant = _tenant(client, email_outbox, "first")
    stored = _publish(tenant)
    assert stored["version"] == 1
    with _session(tenant) as db:
        row = db.get(ScorecardVersion, stored["id"])
        assert row.workspace_id == tenant["workspace_id"]
        assert row.scorecard_key == "inbound_sales"
        assert row.scorecards_version == SCORECARDS_VERSION
        # The document is the validated definition with every default filled in.
        assert row.document["description"] is None
        assert row.document["criteria"][0]["auto_fail"] is False
        assert row.document["criteria"][0]["check"]["scope"] == {
            "kind": "whole_call",
            "max_turns": None,
        }
        assert row.digest == definition_digest(_definition()) == definition_digest(row.document)
        assert row.reason == "first publish"
        assert row.created_by_user_id == tenant["user_id"]
        assert row.created_at.replace(tzinfo=row.created_at.tzinfo or UTC) == NOW


def test_each_publish_is_the_next_version_and_the_same_document_is_still_new(client, email_outbox):
    tenant = _tenant(client, email_outbox, "next")
    first = _publish(tenant)
    second = _publish(tenant, expected=1, reason="same again")
    third = _publish(tenant, expected=2, reason="stricter", definition=_definition(threshold=90))
    assert [first["version"], second["version"], third["version"]] == [1, 2, 3]
    assert first["digest"] == second["digest"] != third["digest"]
    # Earlier rows never change.
    assert [(row[2], row[3]) for row in _all_rows()] == [
        (1, "first publish"),
        (2, "same again"),
        (3, "stricter"),
    ]


def test_a_stale_expected_version_is_a_conflict_and_writes_nothing(client, email_outbox):
    tenant = _tenant(client, email_outbox, "stale")
    for expected in (1, 5):  # no version yet: only 0 is right
        with pytest.raises(ScorecardVersionConflict) as conflict, _session(tenant) as db:
            publish_version(
                db,
                workspace_id=tenant["workspace_id"],
                scorecard_key="inbound_sales",
                definition=_definition(),
                expected_version=expected,
                reason="r",
                actor_user_id=tenant["user_id"],
                now=NOW,
            )
        assert conflict.value.current_version == 0
    assert _all_rows() == []
    _publish(tenant)
    for expected in (0, 2):
        with pytest.raises(ScorecardVersionConflict) as conflict:
            _publish(tenant, expected=expected)
        assert conflict.value.current_version == 1
    assert [row[2] for row in _all_rows()] == [1]


def test_a_concurrent_insert_is_reported_as_the_same_conflict(client, email_outbox, monkeypatch):
    tenant = _tenant(client, email_outbox, "race")
    _publish(tenant)  # version 1 exists
    # The other writer committed version 1 after this one read "no version yet".
    real = scorecard_service.current_version
    calls = []

    def stale_then_real(db, **kwargs):
        calls.append(kwargs)
        return 0 if len(calls) == 1 else real(db, **kwargs)

    monkeypatch.setattr(scorecard_service, "current_version", stale_then_real)
    with pytest.raises(ScorecardVersionConflict) as conflict:
        _publish(tenant, expected=0, reason="lost the race")
    assert conflict.value.current_version == 1
    assert [(row[2], row[3]) for row in _all_rows()] == [(1, "first publish")]
    # The session is still usable after the conflict: the next publish works.
    monkeypatch.setattr(scorecard_service, "current_version", real)
    assert _publish(tenant, expected=1, reason="retry")["version"] == 2


@pytest.mark.parametrize(
    "key", ["Inbound", "1nbound", "a", "", "in-bound", "a" * 65, "inbound_sales\n", None]
)
def test_an_invalid_key_is_refused_before_anything_is_read(client, email_outbox, key):
    tenant = _tenant(client, email_outbox, "key")
    with pytest.raises(ScorecardError) as error:
        _publish(tenant, key=key)
    assert error.value.code == "scorecard_key_invalid"
    assert _all_rows() == []


def test_an_invalid_definition_raises_its_code_and_writes_nothing(client, email_outbox):
    tenant = _tenant(client, email_outbox, "invalid")
    broken = _definition()
    broken["criteria"][0]["weight"] = 90
    with pytest.raises(ScorecardError) as error:
        _publish(tenant, definition=broken)
    assert error.value.code == "weights_invalid"
    assert _all_rows() == []


def test_reads_list_the_latest_of_each_key_every_version_and_one_version(client, email_outbox):
    tenant = _tenant(client, email_outbox, "reads")
    _publish(tenant, key="outbound_sales", reason="o1")
    _publish(tenant, key="inbound_sales", reason="i1")
    _publish(tenant, key="inbound_sales", expected=1, reason="i2")
    _publish(tenant, key="inbound_sales", expected=2, reason="i3", now=NOW + timedelta(days=1))
    with _session(tenant) as db:
        latest = latest_versions(db, workspace_id=tenant["workspace_id"])
        assert [(row.scorecard_key, row.version, row.reason) for row in latest] == [
            ("inbound_sales", 3, "i3"),
            ("outbound_sales", 1, "o1"),
        ]
        history = list_versions(
            db, workspace_id=tenant["workspace_id"], scorecard_key="inbound_sales"
        )
        assert [row.version for row in history] == [3, 2, 1]
        assert list_versions(db, workspace_id=tenant["workspace_id"], scorecard_key="nope") == []
        one = get_version(
            db, workspace_id=tenant["workspace_id"], scorecard_key="inbound_sales", version=2
        )
        assert (one.version, one.reason) == (2, "i2")
        for key, version in (
            ("inbound_sales", 4),
            ("inbound_sales", 0),
            ("inbound_sales", -1),
            ("inbound_sales", 2**31),  # over the integer column: None, not a database error
            ("nope", 1),
        ):
            assert (
                get_version(
                    db, workspace_id=tenant["workspace_id"], scorecard_key=key, version=version
                )
                is None
            )
        assert (
            scorecard_service.current_version(
                db, workspace_id=tenant["workspace_id"], scorecard_key="inbound_sales"
            )
            == 3
        )


def test_a_workspace_sees_only_its_own_versions_and_publishes_its_own_first(client, email_outbox):
    first = _tenant(client, email_outbox, "ws-a")
    second = _tenant(client, email_outbox, "ws-b")
    _publish(first, reason="a1")
    _publish(first, expected=1, reason="a2")
    # B has no version of the same key: it publishes its own version 1.
    assert _publish(second, reason="b1")["version"] == 1
    with _session(second) as db:
        workspace = second["workspace_id"]
        assert [(r.version, r.reason) for r in latest_versions(db, workspace_id=workspace)] == [
            (1, "b1")
        ]
        assert [
            r.reason
            for r in list_versions(db, workspace_id=workspace, scorecard_key="inbound_sales")
        ] == ["b1"]
        # Asking for A's workspace from B's session finds nothing.
        assert latest_versions(db, workspace_id=first["workspace_id"]) == []
        assert (
            get_version(
                db, workspace_id=first["workspace_id"], scorecard_key="inbound_sales", version=2
            )
            is None
        )
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(ScorecardVersion)) == 3
