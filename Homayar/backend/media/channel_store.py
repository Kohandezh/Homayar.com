"""Where a recording's channel-inspection record lives (HOM-37, ADR 0039).

The two helpers below are the only code that knows the location of the
record.  Its target is a write-once JSON column ``media_objects.channel_inspection``
(owner decision, option B).  That column needs a migration, which is not
booked yet, so until then the active store keeps nothing: storing is a no-op
and loading returns ``None`` (``channel_inspection`` is ``null`` on meeting
detail and nothing else changes).

The store is a seam: :func:`use_store` swaps in another implementation (the
tests use an in-memory one keyed by media object id).  When the column
exists, the column store replaces :class:`NoColumnStore` here and nothing
else changes.

The record is written once, while the ORIGINAL media object is registered in
the ingest transaction, and is never rewritten: a later change of the
workspace's channel-role mapping does not touch it.
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.orm import Session

from backend.database.saas_models import MediaObject, Recording


class ChannelInspectionStore(Protocol):
    def store(self, db: Session, *, media_object: MediaObject, record: dict[str, Any]) -> None:
        """Keep ``record`` for ``media_object``; called before the object is inserted."""

    def load(
        self, db: Session, *, workspace_id: str, media_object_id: str
    ) -> dict[str, Any] | None:
        """The record kept for the media object, or None."""


class NoColumnStore:
    """Before the column exists: nothing is kept, nothing is found."""

    def store(self, db: Session, *, media_object: MediaObject, record: dict[str, Any]) -> None:
        return None

    def load(
        self, db: Session, *, workspace_id: str, media_object_id: str
    ) -> dict[str, Any] | None:
        return None


_store: ChannelInspectionStore = NoColumnStore()


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
