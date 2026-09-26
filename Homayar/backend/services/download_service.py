"""Bounded recording delivery with HTTP Range support (WP03-T06).

Two delivery shapes, chosen by the storage backend:

* application-mediated streaming (local storage): every request, including
  each ``Range`` request of a seeking player, passes through the authorization
  that called us, so revocation is immediate; memory use is one chunk;
* presigned redirect (S3-compatible storage): the URL is scoped to exactly
  one object and expires after ``ttl_seconds``; access issued before a
  revocation remains valid until that expiry (expiry-bounded revocation).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from backend.database.saas_models import Recording
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
    if storage.supports_presigned_access:
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
        reader = storage.open_reader(workspace_id, recording.storage_key)
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
    try:
        reader.seek(byte_range.start)
    except (AttributeError, OSError):
        reader.close()
        # Non-seekable backend without presigning: serve the whole object.
        headers["Content-Length"] = str(recording.size_bytes)
        reader = storage.open_reader(workspace_id, recording.storage_key)
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
