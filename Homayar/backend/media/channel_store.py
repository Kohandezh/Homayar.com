"""Where a recording's channel-inspection record lives (HOM-37, ADR 0039).

The two helpers below are the only code that knows the location of the
record: the write-once JSON column ``media_objects.channel_inspection``
(owner decision, option B; migration ``a7c3e9f5b2d1``).

The record is set on the ORIGINAL media object before it is inserted, in the
ingest transaction, and is never rewritten: the column is a content column of
``MediaObject``, so the ORM guard refuses any later change, and a later change
of the workspace's channel-role mapping does not touch it.

The store stays a seam: :func:`use_store` swaps in another implementation.
"""

from __future__ import annotations

import copy
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database.saas_models import MediaObject, Recording


class ChannelInspectionStore(Protocol):
    def store(self, db: Session, *, media_object: MediaObject, record: dict[str, Any]) -> None:
        """Keep ``record`` for ``media_object``; called before the object is inserted."""

    def load(
        self, db: Session, *, workspace_id: str, media_object_id: str
    ) -> dict[str, Any] | None:
        """The record kept for the media object, or None."""


class ColumnStore:
    """``media_objects.channel_inspection``: set at insert, read by id and tenant."""

    def store(self, db: Session, *, media_object: MediaObject, record: dict[str, Any]) -> None:
        if media_object.channel_inspection is not None:
            raise RuntimeError("A channel inspection record is written once")
        media_object.channel_inspection = copy.deepcopy(record)

    def load(
        self, db: Session, *, workspace_id: str, media_object_id: str
    ) -> dict[str, Any] | None:
        value = db.scalar(
            select(MediaObject.channel_inspection).where(
                MediaObject.id == media_object_id,
                MediaObject.workspace_id == workspace_id,
            )
        )
        return copy.deepcopy(value) if isinstance(value, dict) else None


class NoColumnStore:
    """Before the column existed: nothing is kept, nothing is found."""

    def store(self, db: Session, *, media_object: MediaObject, record: dict[str, Any]) -> None:
        return None

    def load(
        self, db: Session, *, workspace_id: str, media_object_id: str
    ) -> dict[str, Any] | None:
        return None


_store: ChannelInspectionStore = ColumnStore()


def use_store(store: ChannelInspectionStore) -> ChannelInspectionStore:
    """Replace the active store; returns the previous one (a test seam)."""
    global _store
    previous, _store = _store, store
    return previous


def store_channel_inspection(
    db: Session, *, media_object: MediaObject, record: dict[str, Any]
) -> None:
    """Keep the record of a newly registered media object (write-once)."""
    _store.store(db, media_object=media_object, record=record)


def load_channel_inspection(db: Session, *, recording: Recording) -> dict[str, Any] | None:
    """The record of the recording's ORIGINAL media object, or None."""
    if recording.media_object_id is None:
        return None
    return _store.load(
        db, workspace_id=recording.workspace_id, media_object_id=recording.media_object_id
    )
