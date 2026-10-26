"""HOM-37 option B: the channel-inspection record in ``media_objects.channel_inspection``.

Runs against the real column (no in-memory store): an upload keeps the record on
its ORIGINAL media object, PostgreSQL returns it unchanged, meeting detail serves
it, and the write-once guard refuses any later change.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.database.saas_models import MediaImmutableError, MediaObject, Recording
from backend.tests._database import inspection_session_factory
from backend.tests.test_channel_roles_api import (
    AGENT_CUSTOMER,
    _inspection,
    _put,
    _roles,
)
from backend.tests.test_ingestion_ledger import _patch_media, _tenant, _upload


def _original(meeting_id: str) -> MediaObject:
    with inspection_session_factory()() as db:
        recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting_id))
        return db.get(MediaObject, recording.media_object_id)


def test_the_record_round_trips_through_the_column(client, email_outbox, monkeypatch):
    _patch_media(monkeypatch)
    owner = _tenant(client, email_outbox, "col-round-trip")
    assert _put(client, owner, AGENT_CUSTOMER).status_code == 200
    response = _upload(client, owner, key="evt-col-0001")
    assert response.status_code == 202, response.text
    meeting_id = response.json()["meeting"]["id"]

    stored = _original(meeting_id).channel_inspection
    assert stored is not None and stored["version"] == "channel-inspection/1"
    served = _inspection(client, owner, meeting_id)
    assert served == stored
    assert _roles(served) == [("unknown", "unknown", "mono")]
    assert served["mapping_version"] == 1


def test_the_column_is_write_once(client, email_outbox, monkeypatch):
    _patch_media(monkeypatch)
    owner = _tenant(client, email_outbox, "col-guard")
    response = _upload(client, owner, key="evt-col-0002")
    assert response.status_code == 202, response.text
    media_id = _original(response.json()["meeting"]["id"]).id

    with inspection_session_factory()() as db:
        media = db.get(MediaObject, media_id)
        before = dict(media.channel_inspection)
        media.channel_inspection = {**before, "status": "complete"}
        with pytest.raises(MediaImmutableError, match="channel_inspection"):
            db.flush()
        db.rollback()
    assert _original(response.json()["meeting"]["id"]).channel_inspection == before


def test_the_column_store_reads_by_tenant(client, email_outbox, monkeypatch):
    from backend.media.channel_store import ColumnStore

    _patch_media(monkeypatch)
    owner = _tenant(client, email_outbox, "col-tenant")
    other = _tenant(client, email_outbox, "col-other")
    response = _upload(client, owner, key="evt-col-0003")
    assert response.status_code == 202, response.text
    media_id = _original(response.json()["meeting"]["id"]).id
    store = ColumnStore()
    with inspection_session_factory()() as db:
        mine = store.load(db, workspace_id=owner["workspace_id"], media_object_id=media_id)
        theirs = store.load(db, workspace_id=other["workspace_id"], media_object_id=media_id)
    assert mine is not None and theirs is None
