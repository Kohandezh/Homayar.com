"""Bounded recording delivery with HTTP Range support (WP03-T06).

Two delivery shapes, chosen by the storage backend:

* application-mediated streaming (local storage, and S3 with
  ``STORAGE_PRESIGNED_DELIVERY=false`` as on-prem, HOM-340): every request,
  including each ``Range`` request of a seeking player, passes through the
  authorization that called us, so revocation is immediate; a ``Range`` reads
  only those bytes from storage; memory use is one chunk;
* presigned redirect (S3-compatible storage, the default): the URL is scoped
  to exactly one object and expires after ``ttl_seconds``; access issued
  before a revocation remains valid until that expiry (expiry-bounded
  revocation).

Before either shape, every route that sends recording bytes checks that the
recording may be played at all (HOM-60): it exists, its media object is live,
and its retention has not expired.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from backend.database.saas_models import Meeting, Recording, Workspace
from backend.services.media_lifecycle_service import LIVE_STATES
from backend.services.retention_policy import is_expiry_due, is_retention_expired
from backend.storage import ObjectNotFoundError, ObjectStorage

CHUNK_BYTES = 1024 * 1024
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int  # inclusive

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_range(header: str | None, size: int) -> ByteRange | None:
    """Parse a single-range ``Range`` header; raise 416 for unsatisfiable ranges."""
    if not header:
        return None
    match = _RANGE.fullmatch(header.strip())
    if match is None or size <= 0:
        raise HTTPException(
            status_code=416,
            detail={"code": "range_not_satisfiable", "message": "Invalid range"},
            headers={"Content-Range": f"bytes */{size}"},
        )
    start_text, end_text = match.groups()
    if start_text == "" and end_text == "":
        raise HTTPException(
            status_code=416,
            detail={"code": "range_not_satisfiable", "message": "Invalid range"},
            headers={"Content-Range": f"bytes */{size}"},
        )
    if start_text == "":
        suffix = int(end_text)
        if suffix == 0:
            raise HTTPException(
                status_code=416,
                detail={"code": "range_not_satisfiable", "message": "Invalid range"},
                headers={"Content-Range": f"bytes */{size}"},
            )
        start = max(size - suffix, 0)
        end = size - 1
    else:
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
        end = min(end, size - 1)
    if start > end or start >= size:
        raise HTTPException(
            status_code=416,
            detail={"code": "range_not_satisfiable", "message": "Range not satisfiable"},
            headers={"Content-Range": f"bytes */{size}"},
        )
    return ByteRange(start=start, end=end)


def _iter_reader(reader, *, remaining: int | None) -> Iterator[bytes]:
    try:
        if remaining is None:
            while chunk := reader.read(CHUNK_BYTES):
                yield chunk
            return
        while remaining > 0:
            chunk = reader.read(min(CHUNK_BYTES, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        reader.close()


def ensure_recording_playable(
    db: Session,
    *,
    workspace: Workspace,
    meeting: Meeting,
    recording: Recording | None,
    now: datetime,
) -> Recording:
    """The one playability rule of the workspace recording routes (HOM-60).

    In order: no recording or a deleted one is ``404``; a media object that
    is being deleted (not live) is ``404``; a conversation the retention
    sweep would expire now is ``410``.  A legal hold or a running job keeps
    the recording playable, exactly as it keeps the sweep away.  Needs the
    tenant-bound session of a workspace request.
    """
    if recording is None or recording.deleted_at is not None:
        raise _recording_not_found()
    media = recording.media_object
    if media is not None and media.lifecycle_state not in LIVE_STATES:
        raise _recording_not_found()
    if is_retention_expired(db, workspace=workspace, meeting=meeting, now=now):
        raise _retention_expired()
    return recording


def ensure_shared_recording_playable(
    *, workspace: Workspace, meeting: Meeting, recording: Recording | None, now: datetime
) -> Recording:
    """The playability rule of the share route, for every audience (HOM-60).

    A share session cannot read jobs or media objects (row-level security),
    so it fails closed: a due expiry is ``410`` even under a legal hold or a
    running job.  The media lifecycle state is not visible here; a deleted
    original also sets ``recording.deleted_at``, which is checked.
    """
    if recording is None or recording.deleted_at is not None:
        raise _recording_not_found()
    if is_expiry_due(workspace, meeting, now=now):
        raise _retention_expired()
    return recording


def _content_disposition(recording: Recording, disposition: str = "attachment") -> str:
    ascii_name = "recording." + recording.container_format.split(",")[0]
    return (
        f'{disposition}; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(recording.original_filename)}"
    )


def deliver_recording(
    *,
    storage: ObjectStorage,
    workspace_id: str,
    recording: Recording,
    range_header: str | None,
    presign_ttl_seconds: int,
    disposition: str = "attachment",
) -> Response:
    """Authorize first, then call this with the exact recording to deliver.

    ``disposition="inline"`` is the playback shape (a media element reads it);
    the bytes, Range handling and revocation semantics are identical."""
    if storage.supports_presigned_access and storage.presigned_delivery:
        try:
            access = storage.presign_get(
                workspace_id, recording.storage_key, expires_in_seconds=presign_ttl_seconds
            )
        except ObjectNotFoundError as exc:
            raise _recording_not_found() from exc
        return RedirectResponse(
            access.url,
            status_code=307,
            headers={"Cache-Control": "no-store", "X-Homayar-Revocation": "expiry-bounded"},
        )

    byte_range = parse_range(range_header, recording.size_bytes)
    try:
        if byte_range is None:
            reader = storage.open_reader(workspace_id, recording.storage_key)
        else:
            reader = storage.open_range_reader(
                workspace_id, recording.storage_key, start=byte_range.start, end=byte_range.end
            )
    except ObjectNotFoundError as exc:
        raise _recording_not_found() from exc

    headers = {
        "Content-Disposition": _content_disposition(recording, disposition),
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "X-Homayar-Revocation": "immediate",
    }
    if byte_range is None:
        headers["Content-Length"] = str(recording.size_bytes)
        return StreamingResponse(
            _iter_reader(reader, remaining=None), media_type=recording.media_type, headers=headers
        )
    headers["Content-Length"] = str(byte_range.length)
    headers["Content-Range"] = f"bytes {byte_range.start}-{byte_range.end}/{recording.size_bytes}"
    return StreamingResponse(
        _iter_reader(reader, remaining=byte_range.length),
        status_code=206,
        media_type=recording.media_type,
        headers=headers,
    )


def _recording_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "recording_not_found", "message": "Recording not found"},
    )


def _retention_expired() -> HTTPException:
    return HTTPException(
        status_code=410,
        detail={"code": "recording_retention_expired", "message": "Recording retention expired"},
    )
