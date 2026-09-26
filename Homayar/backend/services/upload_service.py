"""Bounded staging for multipart uploads before media inspection."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile


class UploadRejectedError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StagedUpload:
    path: Path
    filename: str
    claimed_content_type: str
    size_bytes: int
    sha256: str

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


async def stage_upload(
    upload: UploadFile,
    *,
    quarantine_dir: Path,
    max_bytes: int,
    chunk_size: int = 1024 * 1024,
) -> StagedUpload:
    if max_bytes <= 0 or chunk_size <= 0:
        raise ValueError("Upload limits must be positive")
    if not upload.filename or not upload.filename.strip():
        raise UploadRejectedError("missing_filename", "A filename is required")

    quarantine_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(quarantine_dir, 0o700)
    descriptor, raw_path = tempfile.mkstemp(prefix="upload-", dir=quarantine_dir)
    path = Path(raw_path)
    os.chmod(path, 0o600)
    size_bytes = 0
    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "wb") as destination:
            while True:
                chunk = await upload.read(chunk_size)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise UploadRejectedError(
                        "upload_too_large",
                        f"Upload exceeds the {max_bytes}-byte limit",
                    )
                destination.write(chunk)
                digest.update(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        if size_bytes == 0:
            raise UploadRejectedError("empty_upload", "The uploaded file is empty")
        return StagedUpload(
            path=path,
            filename=upload.filename,
            claimed_content_type=upload.content_type or "application/octet-stream",
            size_bytes=size_bytes,
            sha256=digest.hexdigest(),
        )
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        await upload.close()
