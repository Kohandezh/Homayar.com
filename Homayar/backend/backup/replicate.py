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
from datetime import UTC, datetime, timedelta
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
_VERIFIED_SUFFIX = ".verified"


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


def _root_verified(source: Path) -> dict[str, Path]:
    """Sets of the source root that passed a deep verify, by id (their set dirs)."""
    if source.parent.name != "sets":
        return {}
    found = {}
    for marker in source.parent.glob(f"*{_VERIFIED_SUFFIX}"):
        backup_id = marker.name[: -len(_VERIFIED_SUFFIX)]
        if valid_backup_id(backup_id) and (source.parent / backup_id / MANIFEST_NAME).is_file():
            found[backup_id] = source.parent / backup_id
    return found


def _backfill_directory_markers(source: Path, destination_root: Path) -> list[str]:
    """Carry the verified marker to copies made before markers were carried.

    Only a copy whose sealed manifest is byte-identical to the verified root
    set's, and whose files are all there with the manifest's checksums, gets
    one: a manifest left behind by an interrupted prune is not a set.
    """
    from backend.backup.retention import verified_marker

    backfilled = []
    for backup_id, root_set in sorted(_root_verified(source).items()):
        copy = final_dir(destination_root, backup_id)
        marker = verified_marker(destination_root, backup_id)
        if marker.exists() or not (copy / MANIFEST_NAME).is_file():
            continue
        if (copy / MANIFEST_NAME).read_bytes() != (root_set / MANIFEST_NAME).read_bytes():
            continue
        try:
            verify_set(copy, None, deep=False)
        except BackupInvalid:
            continue
        marker.write_text(datetime.now(UTC).isoformat() + "\n", encoding="ascii")
        backfilled.append(backup_id)
    return backfilled


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
        return {
            "backup_id": backup_id,
            "destination": str(final),
            "status": "already_present",
            "backfilled_markers": _backfill_directory_markers(source, destination_root),
        }
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
        "backfilled_markers": _backfill_directory_markers(source, destination_root),
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


def _marker_key(prefix: str, backup_id: str) -> str:
    return _s3_key(prefix, f"{backup_id}{_VERIFIED_SUFFIX}", "")


def _composite_sha256(path: Path, parts: int) -> str:
    """S3's checksum of a multipart upload: SHA-256 of the parts' SHA-256s, ``-N``."""
    digests = b""
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_PART_SIZE), b""):
            digests += hashlib.sha256(block).digest()
    return f"{base64.b64encode(hashlib.sha256(digests).digest()).decode()}-{parts}"


def _checked_head(
    client, bucket: str, key: str, path: Path, size: int, sha256: str
) -> tuple[bool, dict]:
    """HEAD the stored object: refuse a size or store-checksum mismatch.

    Returns whether the store's own checksum proved the object (one we can
    reproduce), and the HEAD response.
    """
    name = key.rsplit("/", 1)[-1]
    head = client.head_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED")
    if int(head["ContentLength"]) != size:
        raise BackupInvalid("copy_size_mismatch", name)
    remote = head.get("ChecksumSHA256")
    if remote and "-" not in remote:
        if remote != base64.b64encode(bytes.fromhex(sha256)).decode():
            raise BackupInvalid("copy_checksum_mismatch", name)
        return True, head
    if remote:
        parts = remote.rsplit("-", 1)[1]
        if parts.isdigit() and int(parts) == math.ceil(size / _PART_SIZE):
            if remote == _composite_sha256(path, int(parts)):
                return True, head
    return False, head


def _read_back(client, bucket: str, key: str, sha256: str) -> None:
    digest = hashlib.sha256()
    body = client.get_object(Bucket=bucket, Key=key)["Body"]
    for block in iter(lambda: body.read(1024 * 1024), b""):
        digest.update(block)
    if digest.hexdigest() != sha256:
        raise BackupInvalid("copy_checksum_mismatch", key.rsplit("/", 1)[-1])


def _verify_s3_copy(client, bucket: str, key: str, path: Path, size: int, sha256: str) -> str:
    """Prove the stored object is the local file, by SHA-256.

    The store's own checksum is used when it reports one we can reproduce
    (``checksum``); otherwise the object is read back and hashed
    (``download``).  Any mismatch refuses the copy.
    """
    proved, _ = _checked_head(client, bucket, key, path, size, sha256)
    if proved:
        return "checksum"
    _read_back(client, bucket, key, sha256)
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
        _put_marker(client, bucket, prefix, backup_id)
    return {
        "backup_id": backup_id,
        "destination": f"s3://{bucket}/{_s3_key(prefix, backup_id, '')}",
        "status": "copied",
        "files": len(manifest["files"]),
        "bytes": sum(entry["size"] for entry in manifest["files"]),
        "verified_by": verified_by,
        **_backfill_s3_markers(client, bucket, prefix, source),
    }


def _put_marker(client, bucket: str, prefix: str, backup_id: str) -> None:
    client.put_object(
        Bucket=bucket,
        Key=_marker_key(prefix, backup_id),
        Body=(datetime.now(UTC).isoformat() + "\n").encode("ascii"),
        ChecksumAlgorithm="SHA256",
    )


_REJECTED_SUFFIX = ".offsite-rejected"
_MISSING_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


def _backfill_s3_markers(client, bucket: str, prefix: str, source: Path) -> dict[str, Any]:
    """Carry the verified marker to copies made before markers were carried,
    when the copy's sealed manifest is byte-identical to the verified root
    set's and every file it lists is stored with its size and SHA-256 (read
    back when the store reports no usable checksum).

    Best effort: it runs after the new set is copied and verified, so a
    failure here never fails the copy (nor keeps the CLI from moving the
    off-site gauge).  A candidate that does not qualify is reported in
    ``backfill_rejected`` with the reason; a call that fails (a network
    error, a denied request) is reported in ``backfill_errors`` and the
    candidate is tried again on the next run.
    """
    from botocore.exceptions import BotoCoreError, ClientError

    result: dict[str, Any] = {
        "backfilled_markers": [],
        "backfill_rejected": [],
        "backfill_errors": [],
    }
    verified = _root_verified(source)
    if not verified:
        return result
    destination = f"s3://{bucket}/{prefix}" if prefix else f"s3://{bucket}"
    try:
        candidates = set(list_s3_sets(destination, client=client)) & set(verified)
        candidates -= _s3_markers(bucket, prefix, client)
    except (BotoCoreError, ClientError) as exc:
        result["backfill_errors"].append({"step": "list", "error": _error_code(exc)})
        return result
    for backup_id in sorted(candidates):
        try:
            reason = _backfill_rejection(client, bucket, prefix, verified[backup_id], destination)
            if reason is None:
                _put_marker(client, bucket, prefix, backup_id)
        except (BotoCoreError, ClientError) as exc:
            result["backfill_errors"].append({"backup_id": backup_id, "error": _error_code(exc)})
            continue
        if reason is None:
            result["backfilled_markers"].append(backup_id)
        else:
            result["backfill_rejected"].append({"backup_id": backup_id, "reason": reason})
    return result


def _rejection_record(set_dir: Path) -> Path:
    return set_dir.parent / f"{set_dir.name}{_REJECTED_SUFFIX}"


def _remembered_rejection(set_dir: Path, destination: str, etags: dict[str, str]) -> str | None:
    """The reason a read-back already rejected these very objects (same ETags)."""
    try:
        record = json.loads(_rejection_record(set_dir).read_text(encoding="utf-8"))
        entry = record[destination]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if isinstance(entry, dict) and entry.get("etags") == etags:
        return str(entry.get("reason"))
    return None


def _remember_rejection(
    set_dir: Path, destination: str, reason: str, etags: dict[str, str]
) -> None:
    """Record a read-back rejection beside the root set, so the same objects
    are not downloaded again every night.  Best effort; removed with the set."""
    path = _rejection_record(set_dir)
    try:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                record = {}
        except (OSError, ValueError):
            record = {}
        record[destination] = {
            "reason": reason,
            "etags": etags,
            "at": datetime.now(UTC).isoformat(),
        }
        path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        pass


def _backfill_rejection(
    client, bucket: str, prefix: str, set_dir: Path, destination: str
) -> str | None:
    """None when the off-site copy is proven complete, else why it is rejected.

    Every object is first checked by HEAD (presence, size and the store's
    checksum when it has one we can reproduce), so a missing or truncated
    copy is rejected without downloading anything.  Only then are the
    objects without a usable store checksum read back, and a rejection found
    that way is remembered with the objects' ETags: the same objects are not
    downloaded again on later runs, while a copy that is uploaded again
    (new ETags) is checked afresh.  Network and permission errors propagate.
    """
    from botocore.exceptions import ClientError

    manifest = read_manifest(set_dir)
    backup_id = manifest["backup_id"]
    try:
        stored = client.get_object(Bucket=bucket, Key=_s3_key(prefix, backup_id, MANIFEST_NAME))
    except ClientError as exc:
        if _error_code(exc) in _MISSING_CODES:
            return f"missing: {MANIFEST_NAME}"
        raise
    if stored["Body"].read() != (set_dir / MANIFEST_NAME).read_bytes():
        return "manifest_differs: the copy's manifest is not the verified set's"
    unproven: list[dict] = []
    etags: dict[str, str] = {}
    for entry in manifest["files"]:
        key = _s3_key(prefix, backup_id, entry["name"])
        try:
            proved, head = _checked_head(
                client, bucket, key, set_dir / entry["name"], entry["size"], entry["sha256"]
            )
        except BackupInvalid as exc:
            return str(exc)
        except ClientError as exc:
            if _error_code(exc) in _MISSING_CODES:
                return f"missing: {entry['name']}"
            raise
        etags[entry["name"]] = str(head.get("ETag", ""))
        if not proved:
            unproven.append(entry)
    if not unproven:
        return None
    remembered = _remembered_rejection(set_dir, destination, etags)
    if remembered is not None:
        return remembered
    for entry in unproven:
        try:
            _read_back(client, bucket, _s3_key(prefix, backup_id, entry["name"]), entry["sha256"])
        except BackupInvalid as exc:
            _remember_rejection(set_dir, destination, str(exc), etags)
            return str(exc)
    return None


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


_LOCK_CODES = frozenset({"AccessDenied", "ObjectLocked"})
# The object (or the bucket) has no lock configuration: nothing protects it.
_NO_LOCK_CODES = frozenset(
    {"NoSuchObjectLockConfiguration", "ObjectLockConfigurationNotFoundError", "InvalidRequest"}
)


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None) or {}
    return str(response.get("Error", {}).get("Code", "")) or type(exc).__name__


def _set_of(prefix: str, key: str) -> str | None:
    """The backup id a key belongs to (a set object or its ``<id>.verified`` marker)."""
    rest = key[len(prefix) + 1 if prefix else 0 :]
    head = rest.split("/", 1)[0]
    if "/" not in rest and head.endswith(_VERIFIED_SUFFIX):
        head = head[: -len(_VERIFIED_SUFFIX)]
    return head if valid_backup_id(head) else None


def _uploads(
    client, bucket: str, prefix: str, now: datetime, grace: int, errors: list[str]
) -> tuple[list[dict], set[str], bool]:
    """Multipart uploads interrupted longer ago than the grace period, the
    backup ids that still have a live (recent) upload, and whether the
    listing succeeded."""
    from botocore.exceptions import ClientError

    stale: list[dict] = []
    live: set[str] = set()
    request: dict[str, Any] = {"Bucket": bucket, "Prefix": f"{prefix}/" if prefix else ""}
    try:
        while True:
            page = client.list_multipart_uploads(**request)
            for upload in page.get("Uploads", []):
                if (now - upload["Initiated"]).total_seconds() >= grace:
                    stale.append({"Key": upload["Key"], "UploadId": upload["UploadId"]})
                elif backup_id := _set_of(prefix, upload["Key"]):
                    live.add(backup_id)
            if not page.get("IsTruncated"):
                return stale, live, True
            request["KeyMarker"] = page.get("NextKeyMarker", "")
            request["UploadIdMarker"] = page.get("NextUploadIdMarker", "")
    except ClientError as exc:
        errors.append(f"list_multipart_uploads: {_error_code(exc)}")
        return [], set(), False


def _versioning(client, bucket: str, errors: list[str]) -> str:
    from botocore.exceptions import ClientError

    try:
        return client.get_bucket_versioning(Bucket=bucket).get("Status", "") or ""
    except ClientError as exc:
        errors.append(f"get_bucket_versioning: {_error_code(exc)}")
        return "unknown"


def _object_lock(client, bucket: str, errors: list[str]) -> bool:
    """Whether the bucket has Object Lock enabled (AppVision D15)."""
    from botocore.exceptions import ClientError

    try:
        configuration = client.get_object_lock_configuration(Bucket=bucket)
    except ClientError as exc:
        if _error_code(exc) not in _NO_LOCK_CODES:
            errors.append(f"get_object_lock_configuration: {_error_code(exc)}")
        return False
    return configuration.get("ObjectLockConfiguration", {}).get("ObjectLockEnabled") == "Enabled"


def _versions(client, bucket: str, key_prefix: str, errors: list[str]) -> list[dict] | None:
    from botocore.exceptions import ClientError

    found: list[dict] = []
    try:
        paginator = client.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket, Prefix=key_prefix):
            found += [{**v, "marker": False} for v in page.get("Versions", [])]
            found += [{**v, "marker": True} for v in page.get("DeleteMarkers", [])]
    except ClientError as exc:
        errors.append(f"list_object_versions: {_error_code(exc)}")
        return None
    return found


_LEGAL_HOLD = "legal-hold"


def _lock_until(client, bucket: str, item: dict, now: datetime, errors: list[str]) -> Any:
    """Whether an object lock still protects this version: its retain-until date
    (``_LEGAL_HOLD`` for a legal hold), or None when it does not (or cannot be read)."""
    from botocore.exceptions import ClientError

    key, version = item["Key"], item["VersionId"]
    try:
        hold = client.get_object_legal_hold(Bucket=bucket, Key=key, VersionId=version)
        if hold.get("LegalHold", {}).get("Status") == "ON":
            return _LEGAL_HOLD
    except ClientError as exc:
        if _error_code(exc) not in _NO_LOCK_CODES:
            errors.append(f"get_object_legal_hold: {_error_code(exc)}")
            return None
    try:
        retention = client.get_object_retention(Bucket=bucket, Key=key, VersionId=version)
    except ClientError as exc:
        if _error_code(exc) not in _NO_LOCK_CODES:
            errors.append(f"get_object_retention: {_error_code(exc)}")
        return None
    until = retention.get("Retention", {}).get("RetainUntilDate")
    if until is not None and until > now:
        return until
    return None


def _delete_versions(
    client, bucket: str, items: list[dict], lock: bool, counts: dict[str, Any], errors: list[str]
) -> None:
    """Delete exactly these versions and delete markers.

    A delete refused while an object lock (AppVision D15: GOVERNANCE
    retention, or a legal hold) still protects the version is counted as
    ``locked``: that key keeps its delete marker, so the set stays hidden,
    and a later prune retries it (the leftover sweep) once the lock has
    expired.  A refusal is ``locked`` only when the bucket has Object Lock
    and that version has an unexpired retention or a legal hold; anything
    else (a missing ``s3:DeleteObjectVersion``, any other error) is
    ``failed`` and fails the run.
    """
    from botocore.exceptions import ClientError

    now = datetime.now(UTC)
    by_key: dict[str, list[dict]] = {}
    for item in items:
        by_key.setdefault(item["Key"], []).append(item)
    for key, versions in by_key.items():
        kept = False
        # Versions first, delete markers last: a key whose data is still there
        # keeps its marker and is not resurrected as a current object.
        for item in sorted(versions, key=lambda entry: entry["marker"]):
            if item["marker"] and kept:
                continue
            try:
                client.delete_object(Bucket=bucket, Key=key, VersionId=item["VersionId"])
                counts["deleted"] += 1
                continue
            except ClientError as exc:
                code = _error_code(exc)
            kept = True
            until = (
                _lock_until(client, bucket, item, now, errors)
                if lock and code in _LOCK_CODES
                else None
            )
            if until == _LEGAL_HOLD:
                counts["locked"] += 1
                counts["legal_holds"] += 1
            elif until is not None:
                counts["locked"] += 1
                latest = counts["locked_until"]
                counts["locked_until"] = until if latest is None else max(latest, until)
            else:
                counts["failed"] += 1
                if f"delete_object_version: {code}" not in errors:
                    errors.append(f"delete_object_version: {code}")


def _purge_set(
    client,
    bucket: str,
    prefix: str,
    backup_id: str,
    lock: bool,
    counts: dict[str, Any],
    errors: list[str],
) -> None:
    """Delete every version and delete marker of a set and of its marker.

    In a versioned bucket a plain delete only adds a delete marker, so the
    expired set would stay as noncurrent versions.
    """
    for key_prefix in (_s3_key(prefix, backup_id, "") + "/", _marker_key(prefix, backup_id)):
        items = _versions(client, bucket, key_prefix, errors) or []
        _delete_versions(client, bucket, items, lock, counts, errors)


def monthly_lock_until(backup_id: str, *, daily: int, monthly: int) -> datetime:
    """How long Object Lock keeps a monthly set (AppVision D15): the earliest
    day the retention could expire it.

    A set of month M stays a monthly set until each of the ``monthly``
    newer months has a set older than the daily window.  Month M +
    ``monthly`` has one at the earliest on its first day, and that set
    leaves the daily window ``daily`` days later (with a set every day;
    missing days only delay the expiry).  So the lock never outlives the
    retention: the prune can always delete the set once the policy expires
    it, and the lock ends at most a day before that.
    """
    from backend.backup.retention import backup_time

    moment = backup_time(backup_id)
    months = moment.year * 12 + moment.month - 1 + monthly
    first = datetime(months // 12, months % 12 + 1, 1, tzinfo=UTC)
    return first + timedelta(days=daily)


def _extend_retention(
    client, bucket: str, key: str, until: datetime, counts: dict[str, Any], errors: list[str]
) -> None:
    """Extend the current version's GOVERNANCE retention to ``until``, never
    shortening it (that would need ``s3:BypassGovernanceRetention``, which
    the backup credential never has) and never touching another mode."""
    from botocore.exceptions import ClientError

    try:
        current = client.get_object_retention(Bucket=bucket, Key=key).get("Retention", {})
    except ClientError as exc:
        if _error_code(exc) not in _NO_LOCK_CODES:
            if f"get_object_retention: {_error_code(exc)}" not in errors:
                errors.append(f"get_object_retention: {_error_code(exc)}")
            return
        current = {}
    if current.get("Mode") not in (None, "GOVERNANCE"):
        counts["retention_kept"] += 1  # COMPLIANCE: not the tool's to change
        return
    if current.get("RetainUntilDate") is not None and current["RetainUntilDate"] >= until:
        counts["retention_kept"] += 1
        return
    try:
        client.put_object_retention(
            Bucket=bucket, Key=key, Retention={"Mode": "GOVERNANCE", "RetainUntilDate": until}
        )
    except ClientError as exc:
        if f"put_object_retention: {_error_code(exc)}" not in errors:
            errors.append(f"put_object_retention: {_error_code(exc)}")
        return
    counts["retention_extended"] += 1


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
    the retention unseen.  In a versioned bucket each removed set's versions
    are deleted right after the set, and versions an earlier interrupted
    prune left (keys whose current version is a delete marker) are swept.
    Listing errors (a credential without ``s3:ListBucketMultipartUploads``,
    ``s3:ListBucketVersions`` or ``s3:GetBucketVersioning``) and failed
    version deletes do not stop the prune: they are reported in ``errors``
    and the CLI exits non-zero at the end.  A bucket whose versioning cannot
    be read is never swept.

    With Object Lock (AppVision D15), every object of a set kept as a
    monthly set gets its GOVERNANCE retention extended to
    ``monthly_lock_until`` (``s3:PutObjectRetention``): the bucket default
    covers the daily window only.  Retention is never shortened; a denied
    extension is reported in ``errors`` and fails the run.
    """
    from backend.backup.retention import retained, retained_monthly

    bucket, prefix = parse_s3(destination)
    client = client or s3_client()
    errors: list[str] = []
    sets = list_s3_sets(destination, client=client)
    markers = _s3_markers(bucket, prefix, client)
    qualifying = {
        backup_id
        for backup_id in sets
        if backup_id in markers and _s3_set_is_full(client, bucket, prefix, backup_id)
    }
    keep = retained(sets, daily=daily, monthly=monthly, qualifying=qualifying)
    monthly_sets = retained_monthly(sets, daily=daily, monthly=monthly, qualifying=qualifying)
    expired = sorted(set(sets) - keep)
    now = datetime.now(UTC)
    uploads, live, uploads_listed = _uploads(
        client, bucket, prefix, now, partial_grace_seconds, errors
    )
    groups = _s3_objects_by_set(bucket, prefix, client)
    stale = sorted(
        backup_id
        for backup_id, items in groups.items()
        if uploads_listed  # without the upload listing a live copy cannot be told apart
        and backup_id not in sets
        and backup_id not in live  # an upload is still writing it
        and (now - max(item["LastModified"] for item in items)).total_seconds()
        >= partial_grace_seconds
    )
    orphan_markers = sorted(markers - set(sets))
    versioning = _versioning(client, bucket, errors)
    # Only a bucket known to keep versions is swept: "unknown" (no permission
    # to read the setting) is reported and fails the run instead.
    versioned = versioning in {"Enabled", "Suspended"}
    lock = versioned and _object_lock(client, bucket, errors)
    counts: dict[str, Any] = {
        "deleted": 0,
        "locked": 0,
        "failed": 0,
        "locked_until": None,
        "legal_holds": 0,
        "retention_extended": 0,
        "retention_kept": 0,
    }
    purged: set[str] = set()

    def purge(backup_id: str) -> None:
        # Right after the delete: a prune that dies later leaves no orphaned versions.
        if versioned:
            _purge_set(client, bucket, prefix, backup_id, lock, counts, errors)
            purged.add(backup_id)

    if not dry_run:
        for upload in uploads:
            client.abort_multipart_upload(
                Bucket=bucket, Key=upload["Key"], UploadId=upload["UploadId"]
            )
        for backup_id in stale:
            for item in groups.get(backup_id, []):
                client.delete_object(Bucket=bucket, Key=item["Key"])
            purge(backup_id)
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
            purge(backup_id)
        for backup_id in orphan_markers:
            client.delete_object(Bucket=bucket, Key=_marker_key(prefix, backup_id))
            purge(backup_id)
        if lock:
            for backup_id in sorted(monthly_sets):
                until = monthly_lock_until(backup_id, daily=daily, monthly=monthly)
                keys = [item["Key"] for item in groups.get(backup_id, [])]
                if backup_id in markers:
                    keys.append(_marker_key(prefix, backup_id))
                for key in keys:
                    _extend_retention(client, bucket, key, until, counts, errors)
    # Versions an earlier, interrupted prune (or a lock) left behind: sets with
    # no current data at all whose newest delete marker is older than the
    # grace period, neither kept nor in progress.  Only the versions listed
    # here are deleted, so a copy re-uploaded meanwhile keeps its new versions.
    leftovers: list[str] = []
    if versioned:
        by_set: dict[str, list[dict]] = {}
        for item in _versions(client, bucket, f"{prefix}/" if prefix else "", errors) or []:
            if backup_id := _set_of(prefix, item["Key"]):
                by_set.setdefault(backup_id, []).append(item)
        protected = (set(sets) | set(groups) | markers | live) - set(expired) - set(orphan_markers)
        for backup_id, items in sorted(by_set.items()):
            if backup_id in protected or backup_id in purged:
                continue
            if any(item.get("IsLatest") and not item["marker"] for item in items):
                continue  # current data: not a leftover
            newest = max((item["LastModified"] for item in items if item["marker"]), default=None)
            if newest is None or (now - newest).total_seconds() < partial_grace_seconds:
                continue
            leftovers.append(backup_id)
            if not dry_run:
                _delete_versions(client, bucket, items, lock, counts, errors)
    return {
        "kept": sorted(keep),
        "expired": expired,
        "stale_partials": stale,
        "qualifying": sorted(qualifying),
        "aborted_uploads": [upload["Key"] for upload in uploads],
        "versioning": versioning,
        "object_lock": lock,
        "versions_deleted": counts["deleted"],
        "versions_locked": counts["locked"],
        # The latest retain-until date of a refused version; legal holds have no date.
        "locked_until": counts["locked_until"] and counts["locked_until"].isoformat(),
        "legal_holds": counts["legal_holds"],
        "versions_failed": counts["failed"],
        # D15: monthly sets are locked until the retention could expire them.
        "monthly_locked_until": {
            backup_id: monthly_lock_until(backup_id, daily=daily, monthly=monthly).isoformat()
            for backup_id in sorted(monthly_sets)
        }
        if lock
        else {},
        "retention_extended": counts["retention_extended"],
        "retention_kept": counts["retention_kept"],
        "leftover_sets": leftovers,
        "errors": errors,
    }
