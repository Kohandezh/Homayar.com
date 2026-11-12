"""Streaming recording routes release the DB session before the body (HOM-379).

FastAPI 0.133 runs the exit code of request-scoped ``yield`` dependencies only
after the whole response is sent (``fastapi/routing.py`` closes the request
stack after ``await response(scope, receive, send)``). FastAPI 0.116.1 closed
them before the response was sent. ``get_db`` only closes the session at exit,
so with the new order a route that streams a recording keeps its pooled
connection in an open transaction for the whole stream: on-prem delivery
(``STORAGE_PRESIGNED_DELIVERY=false``) streams through the API, and enough slow
listeners would exhaust the pool. These tests pin the old order for every
route whose body is produced after the endpoint returns: the download, inline
playback and share recording routes.
"""

from __future__ import annotations

from typing import Any

import pytest

import backend.services.download_service as download_service
from backend.database.relational import get_db
from backend.services.download_service import CHUNK_BYTES
from backend.tests.test_recording_delivery_onprem import _OnS3

# Bigger than one delivery chunk, so the body really streams in parts.
OBJECT_SIZE = 2 * CHUNK_BYTES + 12_345


class _CloseRecordingSession:
    """A Session proxy that records every ``close`` into ``events``."""

    def __init__(self, session, events: list[str]) -> None:
        self._session = session
        self._events = events

    def close(self) -> None:
        self._events.append("session_closed")
        self._session.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


@pytest.fixture
def streamed(client, email_outbox, settings, monkeypatch) -> tuple[_OnS3, list[str]]:
    """The on-prem streaming setup, with the session close and chunk reads on one clock."""
    on_s3 = _OnS3(client, email_outbox, settings, monkeypatch)
    monkeypatch.setattr(settings, "storage_presigned_delivery", False)
    events: list[str] = []

    def tracked_get_db():
        gen = get_db()
        session = _CloseRecordingSession(next(gen), events)
        try:
            yield session
        finally:
            gen.close()

    client.app.dependency_overrides[get_db] = tracked_get_db

    real_iter_reader = download_service._iter_reader

    def iter_reader_events(reader, *, remaining):
        for chunk in real_iter_reader(reader, remaining=remaining):
            events.append("chunk_read")
            yield chunk

    monkeypatch.setattr(download_service, "_iter_reader", iter_reader_events)
    yield on_s3, events
    client.app.dependency_overrides.pop(get_db, None)


ROUTES: dict[str, Any] = {
    "download": lambda p: {
        "url": f"/api/v1/meetings/{p.meeting_id}/recording",
        "cookies": p.owner_cookies,
        "headers": {"X-Workspace-ID": p.workspace_id},
    },
    "inline": lambda p: {
        "url": f"/api/v1/workspaces/{p.workspace_id}/meetings/{p.meeting_id}/recording",
        "cookies": p.owner_cookies,
        "headers": {"X-Workspace-ID": p.workspace_id},
    },
    "share": lambda p: {
        "url": f"/api/v1/shares/{p.token}/recording",
        "cookies": p.guest_cookies,
        "headers": {},
    },
}


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_streaming_releases_the_db_session_before_the_first_chunk(streamed, client, route):
    on_s3, events = streamed
    spec = ROUTES[route](on_s3.playable)

    with client.stream(
        "GET", spec["url"], cookies=spec["cookies"], headers=spec["headers"]
    ) as response:
        assert response.status_code == 200, dict(response.headers)
        assert int(response.headers["content-length"]) == OBJECT_SIZE
        first = next(response.iter_bytes())
        assert first
        assert "chunk_read" in events, events
        assert "session_closed" in events, (
            "the DB session was still open while the recording body streamed: a slow "
            f"listener keeps its pooled connection for the whole stream (events: {events})"
        )
        assert events.index("session_closed") < events.index("chunk_read"), events
