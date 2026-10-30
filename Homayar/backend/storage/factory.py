"""Runtime object-storage construction."""

from __future__ import annotations

from pathlib import Path

from backend.config import Settings
from backend.storage.base import ObjectStorage
from backend.storage.local import LocalObjectStorage
from backend.storage.s3 import S3ObjectStorage


def create_object_storage(settings: Settings) -> ObjectStorage:
    max_size_bytes = settings.max_upload_size_mb * 1024 * 1024
    if settings.storage_backend == "local":
        return LocalObjectStorage(
            Path(settings.storage_local_root),
            max_size_bytes=max_size_bytes,
        )
    return S3ObjectStorage(
        settings.storage_bucket,
        max_size_bytes=max_size_bytes,
        endpoint_url=settings.storage_endpoint_url or None,
        region_name=settings.storage_region or None,
        access_key_id=settings.storage_access_key or None,
        secret_access_key=settings.storage_secret_key or None,
        presign_ttl_seconds=settings.storage_presign_ttl_seconds,
        presigned_delivery=settings.storage_presigned_delivery,
        force_path_style=bool(settings.storage_endpoint_url),
    )
