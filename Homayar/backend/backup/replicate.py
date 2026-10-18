"""Copy complete, encrypted sets off the application host (HOM-153/116).

Destinations:

* a directory (a mount of another host or medium: NFS/SMB share, removable
  disk, rclone mount) — ``/mnt/offsite/homayar``;
* an S3-compatible bucket on another host — ``s3://bucket/prefix`` with
  ``HOMAYAR_BACKUP_S3_ENDPOINT`` / ``_ACCESS_KEY`` / ``_SECRET_KEY`` /
  ``_REGION``.

The copy never needs the backup key and never decrypts anything.  Files go
first and the manifest last, so a destination set without a manifest (or a
``.partial-`` directory) is an unfinished copy.  Directory copies are
checksum-verified against the manifest before they are renamed into place;
``fetch`` pulls an S3 copy back into a local root and verifies it the same
way before a restore.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend.backup.crypto import file_sha256
from backend.backup.manifest import (
    MANIFEST_NAME,
    BackupInvalid,
    final_dir,
    partial_dir,
    read_manifest,
    sets_dir,
    valid_backup_id,
)
from backend.backup.verify import verify_set


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(source, "rb") as reader:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            for block in iter(lambda: reader.read(1024 * 1024), b""):
                view = memoryview(block)
                while view:
                    view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)


OFFSITE_MARKER = ".homayar-offsite"


def check_offsite_directory(destination_root: Path, backup_root: Path) -> None:
    """A directory target counts as off-site only with positive evidence.

    The operator writes ``.homayar-offsite`` on the remote medium when it is
    provisioned.  An unmounted share (Docker creates a missing bind source;
    an empty mountpoint stays a local directory) has no marker, so the copy
    is refused instead of landing on the application host while the
    off-site gauge says otherwise.  A target inside the backup root, or one
    that contains it, is never off-site.
    """
    destination = Path(destination_root).resolve()
    root = Path(backup_root).resolve()
    if destination == root or root in destination.parents or destination in root.parents:
        raise BackupInvalid("offsite_inside_backup_root", str(destination))
    if not (destination / OFFSITE_MARKER).is_file():
        raise BackupInvalid(
            "offsite_not_provisioned",
            f"{destination} has no {OFFSITE_MARKER} marker: is the off-site medium mounted?",
        )


def _source_verified(source: Path, backup_id: str) -> bool:
    """The set passed a deep verify where it was made (``sets/<id>.verified``)."""
    from backend.backup.retention import verified_marker

    return (
        source.parent.name == "sets" and verified_marker(source.parent.parent, backup_id).is_file()
    )


def _carry_verified(source: Path, destination_root: Path, backup_id: str) -> None:
    """An off-site copy of a deep-verified set counts as verified there too, so
    the off-site retention prefers the same full verified sets as the root."""
    from backend.backup.retention import verified_marker

    if _source_verified(source, backup_id):
        verified_marker(destination_root, backup_id).write_text(
            datetime.now(UTC).isoformat() + "\n", encoding="ascii"
        )


def replicate_to_directory(set_dir: Path, destination_root: Path) -> dict[str, Any]:
    source = Path(set_dir)
    check_offsite_directory(destination_root, source.parent.parent)
    verify_set(source, None, deep=False)
    manifest = read_manifest(source)
    backup_id = manifest["backup_id"]
    destination_root = Path(destination_root)
    final = final_dir(destination_root, backup_id)
    if final.exists():
        verify_set(final, None, deep=False)
        _carry_verified(source, destination_root, backup_id)
        return {"backup_id": backup_id, "destination": str(final), "status": "already_present"}
    sets_dir(destination_root).mkdir(parents=True, exist_ok=True, mode=0o700)
    work = partial_dir(destination_root, backup_id)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(mode=0o700)
    try:
        for entry in manifest["files"]:
            _copy_file(source / entry["name"], work / entry["name"])
            digest, size = file_sha256(work / entry["name"])
            if digest != entry["sha256"] or size != entry["size"]:
                raise BackupInvalid("copy_checksum_mismatch", entry["name"])
        _copy_file(source / MANIFEST_NAME, work / MANIFEST_NAME)
        os.rename(work, final)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    verify_set(final, None, deep=False)
    _carry_verified(source, destination_root, backup_id)
    return {
        "backup_id": backup_id,
        "destination": str(final),
        "status": "copied",
        "files": len(manifest["files"]),
        "bytes": sum(entry["size"] for entry in manifest["files"]),
    }


# --- S3-compatible destination ------------------------------------------------


def parse_s3(destination: str) -> tuple[str, str]:
    parsed = urlparse(destination)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError("S3 destinations look like s3://bucket/prefix")
    return parsed.netloc, parsed.path.strip("/")


def s3_client(env: dict[str, str] | None = None):
    import boto3
    from botocore.config import Config

    environ = os.environ if env is None else env
    return boto3.client(
        "s3",
        endpoint_url=environ.get("HOMAYAR_BACKUP_S3_ENDPOINT") or None,
        aws_access_key_id=environ.get("HOMAYAR_BACKUP_S3_ACCESS_KEY") or None,
        aws_secret_access_key=environ.get("HOMAYAR_BACKUP_S3_SECRET_KEY") or None,
        region_name=environ.get("HOMAYAR_BACKUP_S3_REGION") or "us-east-1",
        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 5}),
    )


def _s3_key(prefix: str, backup_id: str, name: str) -> str:
    return "/".join(part for part in (prefix, backup_id, name) if part)


_PART_SIZE = 64 * 1024 * 1024
_VERIFIED_SUFFIX = ".verified"


def _marker_key(prefix: str, backup_id: str) -> str:
    return _s3_key(prefix, f"{backup_id}{_VERIFIED_SUFFIX}", "")


def _composite_sha256(path: Path, parts: int) -> str:
    """S3's checksum of a multipart upload: SHA-256 of the parts' SHA-256s, ``-N``."""
    digests = b""
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_PART_SIZE), b""):
            digests += hashlib.sha256(block).digest()
    return f"{base64.b64encode(hashlib.sha256(digests).digest()).decode()}-{parts}"


def _verify_s3_copy(client, bucket: str, key: str, path: Path, size: int, sha256: str) -> str:
    """Prove the stored object is the local file, by SHA-256.

    The store's own checksum is used when it reports one we can reproduce
    (``checksum``); otherwise the object is read back and hashed
    (``download``).  Any mismatch refuses the copy.
    """
    name = key.rsplit("/", 1)[-1]
    head = client.head_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED")
    if int(head["ContentLength"]) != size:
        raise BackupInvalid("copy_size_mismatch", name)
    remote = head.get("ChecksumSHA256")
    if remote and "-" not in remote:
        if remote != base64.b64encode(bytes.fromhex(sha256)).decode():
            raise BackupInvalid("copy_checksum_mismatch", name)
        return "checksum"
    if remote:
        parts = remote.rsplit("-", 1)[1]
        if parts.isdigit() and int(parts) == math.ceil(size / _PART_SIZE):
            if remote == _composite_sha256(path, int(parts)):
                return "checksum"
    digest = hashlib.sha256()
    body = client.get_object(Bucket=bucket, Key=key)["Body"]
    for block in iter(lambda: body.read(1024 * 1024), b""):
        digest.update(block)
    if digest.hexdigest() != sha256:
        raise BackupInvalid("copy_checksum_mismatch", name)
    return "download"


def replicate_to_s3(set_dir: Path, destination: str, *, client=None) -> dict[str, Any]:
    from boto3.s3.transfer import TransferConfig

    bucket, prefix = parse_s3(destination)
    client = client or s3_client()
    source = Path(set_dir)
    verify_set(source, None, deep=False)
    manifest = read_manifest(source)
    backup_id = manifest["backup_id"]
    transfer = TransferConfig(multipart_threshold=_PART_SIZE, multipart_chunksize=_PART_SIZE)
    verified_by = {"checksum": 0, "download": 0}
    manifest_digest, manifest_size = file_sha256(source / MANIFEST_NAME)
    uploads = [(entry["name"], entry["size"], entry["sha256"]) for entry in manifest["files"]]
    # The manifest goes last: it is what makes the copy a listed set.
    uploads.append((MANIFEST_NAME, manifest_size, manifest_digest))
    for name, size, sha256 in uploads:
        object_key = _s3_key(prefix, backup_id, name)
        client.upload_file(
            str(source / name),
            bucket,
            object_key,
            ExtraArgs={"ChecksumAlgorithm": "SHA256"},
            Config=transfer,
        )
        verified_by[_verify_s3_copy(client, bucket, object_key, source / name, size, sha256)] += 1
    if _source_verified(source, backup_id):
        client.put_object(
            Bucket=bucket,
            Key=_marker_key(prefix, backup_id),
            Body=(datetime.now(UTC).isoformat() + "\n").encode("ascii"),
            ChecksumAlgorithm="SHA256",
        )
    return {
        "backup_id": backup_id,
        "destination": f"s3://{bucket}/{_s3_key(prefix, backup_id, '')}",
        "status": "copied",
        "files": len(manifest["files"]),
        "bytes": sum(entry["size"] for entry in manifest["files"]),
        "verified_by": verified_by,
    }


def list_s3_sets(destination: str, *, client=None) -> list[str]:
    """Backup ids whose manifest (the completion marker) is in the bucket."""
    bucket, prefix = parse_s3(destination)
    client = client or s3_client()
    found = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/" if prefix else ""):
        for item in page.get("Contents", []):
            parts = item["Key"][len(prefix) + 1 if prefix else 0 :].split("/")
            if len(parts) == 2 and parts[1] == MANIFEST_NAME and valid_backup_id(parts[0]):
                found.add(parts[0])
    return sorted(found)


def fetch_from_s3(destination: str, backup_id: str, local_root: Path, *, client=None) -> Path:
    """Download one set into ``local_root/sets/<id>`` and verify its checksums."""
    if not valid_backup_id(backup_id):
        raise ValueError("invalid backup id")
    bucket, prefix = parse_s3(destination)
    client = client or s3_client()
    local_root = Path(local_root)
    sets_dir(local_root).mkdir(parents=True, exist_ok=True, mode=0o700)
    work = partial_dir(local_root, backup_id)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(mode=0o700)
    try:
        client.download_file(
            bucket, _s3_key(prefix, backup_id, MANIFEST_NAME), str(work / MANIFEST_NAME)
        )
        manifest = read_manifest(work)
        if manifest["backup_id"] != backup_id:
            raise BackupInvalid("manifest_mismatch", "remote manifest names another backup")
        for entry in manifest["files"]:
            target = work / entry["name"]
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            client.download_file(bucket, _s3_key(prefix, backup_id, entry["name"]), str(target))
            digest, size = file_sha256(target)
            if digest != entry["sha256"] or size != entry["size"]:
                raise BackupInvalid("copy_checksum_mismatch", entry["name"])
        final = final_dir(local_root, backup_id)
        if final.exists():
            raise BackupInvalid("already_present", str(final))
        os.rename(work, final)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    verify_set(final, None, deep=False)
    return final


def _s3_objects_by_set(bucket: str, prefix: str, client) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/" if prefix else ""):
        for item in page.get("Contents", []):
            rest = item["Key"][len(prefix) + 1 if prefix else 0 :]
            backup_id = rest.split("/", 1)[0]
            if valid_backup_id(backup_id):
                groups.setdefault(backup_id, []).append(item)
    return groups


def _s3_markers(bucket: str, prefix: str, client) -> set[str]:
    found = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/" if prefix else ""):
        for item in page.get("Contents", []):
            rest = item["Key"][len(prefix) + 1 if prefix else 0 :]
            if "/" not in rest and rest.endswith(_VERIFIED_SUFFIX):
                backup_id = rest[: -len(_VERIFIED_SUFFIX)]
                if valid_backup_id(backup_id):
                    found.add(backup_id)
    return found


def _s3_set_is_full(client, bucket: str, prefix: str, backup_id: str) -> bool:
    try:
        body = client.get_object(Bucket=bucket, Key=_s3_key(prefix, backup_id, MANIFEST_NAME))
        manifest = json.loads(body["Body"].read())
    except Exception:  # noqa: BLE001 - an unreadable manifest simply does not qualify
        return False
    objects = manifest.get("stores", {}).get("objects", {})
    return bool(objects.get("included")) and objects.get("integrity", "complete") == "complete"


def _stale_uploads(client, bucket: str, prefix: str, now: datetime, grace: int) -> list[dict]:
    """Multipart uploads interrupted longer ago than the grace period."""
    stale: list[dict] = []
    request: dict[str, Any] = {"Bucket": bucket, "Prefix": f"{prefix}/" if prefix else ""}
    while True:
        page = client.list_multipart_uploads(**request)
        for upload in page.get("Uploads", []):
            if (now - upload["Initiated"]).total_seconds() >= grace:
                stale.append({"Key": upload["Key"], "UploadId": upload["UploadId"]})
        if not page.get("IsTruncated"):
            return stale
        request["KeyMarker"] = page.get("NextKeyMarker", "")
        request["UploadIdMarker"] = page.get("NextUploadIdMarker", "")


def _versioning(client, bucket: str) -> str:
    from botocore.exceptions import ClientError

    try:
        return client.get_bucket_versioning(Bucket=bucket).get("Status", "") or ""
    except ClientError:
        return "unknown"


def _purge_versions(client, bucket: str, key_prefix: str) -> tuple[int, int]:
    """Delete every version and delete marker under ``key_prefix``.

    In a versioned bucket a plain delete only adds a delete marker, so the
    expired set would stay as noncurrent versions.  Versions an object lock
    still protects cannot be deleted: they are counted and expire with the
    lock (keep the lock period within the retention).
    """
    from botocore.exceptions import ClientError

    deleted = locked = 0
    paginator = client.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket, Prefix=key_prefix):
        for item in page.get("Versions", []) + page.get("DeleteMarkers", []):
            try:
                client.delete_object(Bucket=bucket, Key=item["Key"], VersionId=item["VersionId"])
                deleted += 1
            except ClientError:
                locked += 1
    return deleted, locked


def prune_s3(
    destination: str,
    *,
    daily: int,
    monthly: int,
    dry_run: bool = False,
    client=None,
    partial_grace_seconds: int = 24 * 3600,
) -> dict[str, Any]:
    """Apply the retention policy to an S3 off-site copy.

    Complete sets (with a manifest) follow ``retained``, preferring per
    bucket a *qualifying* set (deep-verified at the source, so it carries a
    ``<id>.verified`` marker, and full: objects included, not degraded), as
    the backup root does.  Copies WITHOUT a manifest (an interrupted upload
    or prune) and multipart uploads interrupted longer ago than the grace
    period are removed, so encrypted copies of deleted data never outlive
    the retention unseen; in a versioned bucket the removed sets' noncurrent
    versions are deleted too.
    """
    from backend.backup.retention import retained

    bucket, prefix = parse_s3(destination)
    client = client or s3_client()
    sets = list_s3_sets(destination, client=client)
    markers = _s3_markers(bucket, prefix, client)
    qualifying = {
        backup_id
        for backup_id in sets
        if backup_id in markers and _s3_set_is_full(client, bucket, prefix, backup_id)
    }
    keep = retained(sets, daily=daily, monthly=monthly, qualifying=qualifying)
    expired = sorted(set(sets) - keep)
    now = datetime.now(UTC)
    groups = _s3_objects_by_set(bucket, prefix, client)
    stale = sorted(
        backup_id
        for backup_id, items in groups.items()
        if backup_id not in sets
        and (now - max(item["LastModified"] for item in items)).total_seconds()
        >= partial_grace_seconds
    )
    orphan_markers = sorted(markers - set(sets))
    uploads = _stale_uploads(client, bucket, prefix, now, partial_grace_seconds)
    versioning = _versioning(client, bucket)
    versions_deleted = versions_locked = 0
    if not dry_run:
        for upload in uploads:
            client.abort_multipart_upload(
                Bucket=bucket, Key=upload["Key"], UploadId=upload["UploadId"]
            )
        for backup_id in stale:
            for item in groups.get(backup_id, []):
                client.delete_object(Bucket=bucket, Key=item["Key"])
        paginator = client.get_paginator("list_objects_v2")
        for backup_id in expired:
            # Data first, then the marker, manifest last: an interrupted prune
            # leaves a listed set that no longer qualifies and is pruned again
            # next run, never unlisted leftovers that outlive the retention.
            manifest_key = _s3_key(prefix, backup_id, MANIFEST_NAME)
            for page in paginator.paginate(
                Bucket=bucket, Prefix=_s3_key(prefix, backup_id, "") + "/"
            ):
                for item in page.get("Contents", []):
                    if item["Key"] != manifest_key:
                        client.delete_object(Bucket=bucket, Key=item["Key"])
            client.delete_object(Bucket=bucket, Key=_marker_key(prefix, backup_id))
            client.delete_object(Bucket=bucket, Key=manifest_key)
        for backup_id in orphan_markers:
            client.delete_object(Bucket=bucket, Key=_marker_key(prefix, backup_id))
        if versioning in {"Enabled", "Suspended", "unknown"}:
            for backup_id in sorted(set(expired) | set(stale) | set(orphan_markers)):
                for key_prefix in (
                    _s3_key(prefix, backup_id, "") + "/",
                    _marker_key(prefix, backup_id),
                ):
                    deleted, locked = _purge_versions(client, bucket, key_prefix)
                    versions_deleted += deleted
                    versions_locked += locked
    return {
        "kept": sorted(keep),
        "expired": expired,
        "stale_partials": stale,
        "qualifying": sorted(qualifying),
        "aborted_uploads": [upload["Key"] for upload in uploads],
        "versioning": versioning,
        "versions_deleted": versions_deleted,
        "versions_locked": versions_locked,
    }
