"""Shared object-storage contract suite (WP05-T05).

One set of guarantees, run against every supported ``ObjectStorage``:

* ``local`` — the real ``LocalObjectStorage`` on a disposable directory;
* ``s3`` — ``S3ObjectStorage`` over a controlled fake client that models
  conditional creates, 403/404/412, transport failures, pagination and
  response bodies (adapter-contract evidence only: a fake cannot establish
  cloud IAM policy or the behaviour of every S3-compatible service);
* ``live`` — ``S3ObjectStorage`` over a real boto3 client against a
  *designated disposable* bucket, opt-in only (``backend/tests/_live_s3.py``);
  skipped by default and reported as NOT_RUN / BLOCKED_EXTERNAL;
* ``minio`` (id ``s3-minio``, HOM-36) — ``S3ObjectStorage`` over a real boto3
  client against a disposable local MinIO container started by
  ``infra/ci/minio-contract-target.sh`` with the bucket, private access and
  ``homayar-app`` policy of the on-prem Compose profile, opt-in only
  (``backend/tests/_minio_s3.py``); skipped as NOT_RUN without it.

Fault injection (denied / transient / missing bucket) exists for ``local``
and ``s3`` only; tests that need it skip the live and MinIO targets instead
of pretending.  On MinIO the same outcomes come from real decisions instead:
a user with no policy, unsigned, expired and tampered requests, a bucket
outside the app policy and a real multipart upload (the ``minio_s3`` cases
near the end).  The single-request upload proof at the end runs the *actual*
boto3 SDK path through ``botocore.stub.Stubber`` and records every
operation name the adapter issues.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import inspect
import json
import os
import pathlib
import re
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from io import BytesIO
from itertools import islice
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from backend.storage import (
    ChecksumMismatchError,
    InvalidObjectKeyError,
    InvalidStreamError,
    InvalidTenantError,
    LocalObjectStorage,
    ObjectConflictError,
    ObjectNotFoundError,
    ObjectStorage,
    ObjectTooLargeError,
    PresignedAccessNotSupportedError,
    S3ObjectStorage,
    StorageError,
    StoredObject,
)
from backend.storage import s3 as s3_module
from backend.storage.base import tenant_prefix
from backend.tests._live_s3 import (
    LIVE_S3_MAX_OBJECT_BYTES,
    live_s3_target,
    live_s3_unavailable_reason,
    quiet_sdk_logging,
)
from backend.tests._minio_s3 import (
    MINIO_ACCESS_KEY_ENV,
    MINIO_BUCKET_ENV,
    MINIO_DENIED_ACCESS_KEY_ENV,
    MINIO_DENIED_SECRET_KEY_ENV,
    MINIO_ENDPOINT_ENV,
    MINIO_OPT_IN_ENV,
    MINIO_PROBE_ACCESS_KEY_ENV,
    MINIO_PROBE_SECRET_KEY_ENV,
    MINIO_SECRET_KEY_ENV,
    MinioS3Target,
    minio_target,
    minio_unavailable_reason,
)

MAX_OBJECT_BYTES = 4096
TOKEN_A = "contract-token-aaaaaaaa0001"  # noqa: S105 - object token, not a credential
TOKEN_B = "contract-token-bbbbbbbb0002"  # noqa: S105 - object token, not a credential
TOKEN_C = "contract-token-cccccccc0003"  # noqa: S105 - object token, not a credential
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

_OP_NAMES = {
    "put": "PutObject",
    "head": "HeadObject",
    "head_bucket": "HeadBucket",
    "get": "GetObject",
    "delete": "DeleteObject",
    "list": "ListObjectsV2",
}
_MULTIPART_OPERATIONS = {
    "CreateMultipartUpload",
    "UploadPart",
    "UploadPartCopy",
    "CompleteMultipartUpload",
    "AbortMultipartUpload",
    "ListMultipartUploads",
    "ListParts",
}


class StreamInterrupted(Exception):
    """Raised by a test stream to model a client that stops mid-upload."""


def _client_error(code: str, status: int, operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


# --------------------------------------------------------------------------- #
# Controlled fake S3 client
# --------------------------------------------------------------------------- #


class _TrackingBody(BytesIO):
    def __init__(self, data: bytes, register: Callable[[_TrackingBody], None]) -> None:
        super().__init__(data)
        register(self)


class ContractS3Client:
    """Stateful fake modelling the S3 behaviours the adapter depends on.

    * ``IfNoneMatch="*"`` on an existing key → 412 ``PreconditionFailed``;
    * ``head_object`` on a missing key → ``ClientError`` code ``404`` (as boto3
      reports it); ``get_object`` → ``NoSuchKey`` 404;
    * ``bucket_exists=False`` models MinIO on a missing bucket: ``head_object``
      and ``head_bucket`` answer a bodiless ``ClientError`` code ``404`` with
      HTTP 404 for every key;
    * ``list_objects_v2`` paginates with ``page_size`` and can emit an empty
      truncated first page (permitted by the S3 API);
    * per-operation faults: ``deny`` (403 ``AccessDenied``), ``transient``
      (``EndpointConnectionError``), ``bucket_missing`` (``NoSuchBucket`` 404),
      ``incomplete`` (``get_object`` without a body).

    Every API operation name is appended to ``operations``.
    """

    def __init__(
        self, *, page_size: int = 2, empty_first_page: bool = False, bucket_exists: bool = True
    ) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.operations: list[str] = []
        self.faults: dict[str, str] = {}
        self.bodies: list[_TrackingBody] = []
        self.page_size = page_size
        self.empty_first_page = empty_first_page
        self.bucket_exists = bucket_exists

    def _enter(self, op: str) -> str | None:
        name = _OP_NAMES[op]
        self.operations.append(name)
        mode = self.faults.get(op)
        if mode == "deny":
            raise _client_error("AccessDenied", 403, name)
        if mode == "transient":
            raise EndpointConnectionError(endpoint_url="https://objects.invalid")
        if mode == "bucket_missing":
            raise _client_error("NoSuchBucket", 404, name)
        return mode

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self._enter("put")
        key = kwargs["Key"]
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise _client_error("PreconditionFailed", 412, "PutObject")
        body = kwargs["Body"].read()
        assert len(body) == kwargs["ContentLength"], "ContentLength must match the bytes sent"
        self.objects[key] = {**kwargs, "Body": body, "LastModified": datetime.now(UTC)}
        return {"ETag": '"fake"'}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self._enter("head")
        if not self.bucket_exists:
            raise _client_error("404", 404, "HeadObject")
        stored = self.objects.get(kwargs["Key"])
        if stored is None:
            raise _client_error("404", 404, "HeadObject")
        return {"ContentLength": len(stored["Body"]), "Metadata": stored["Metadata"]}

    def head_bucket(self, **kwargs: Any) -> dict[str, Any]:
        self._enter("head_bucket")
        if not self.bucket_exists:
            raise _client_error("404", 404, "HeadBucket")
        return {}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        mode = self._enter("get")
        stored = self.objects.get(kwargs["Key"])
        if stored is None:
            raise _client_error("NoSuchKey", 404, "GetObject")
        if mode == "incomplete":
            return {"ContentLength": len(stored["Body"])}
        return {"Body": _TrackingBody(stored["Body"], self.bodies.append)}

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self._enter("delete")
        self.objects.pop(kwargs["Key"], None)
        return {}

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self._enter("list")
        prefix = kwargs.get("Prefix", "")
        continuation = kwargs.get("ContinuationToken")
        if continuation is None and self.empty_first_page:
            return {"Contents": [], "IsTruncated": True, "NextContinuationToken": "0"}
        matching = [
            {"Key": key, "Size": len(stored["Body"]), "LastModified": stored["LastModified"]}
            for key, stored in sorted(self.objects.items())
            if key.startswith(prefix)
        ]
        start = int(continuation) if continuation is not None else 0
        page = matching[start : start + self.page_size]
        truncated = start + self.page_size < len(matching)
        response: dict[str, Any] = {"Contents": page, "IsTruncated": truncated}
        if truncated:
            response["NextContinuationToken"] = str(start + self.page_size)
        return response

    def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        return f"https://objects.test/{kwargs['Params']['Key']}?signed=1"


# --------------------------------------------------------------------------- #
# Fault injection for the local adapter (filesystem faults below the root)
# --------------------------------------------------------------------------- #


class LocalFaults:
    """Inject OSError faults into the filesystem calls the local adapter uses."""

    _TARGETS = {
        "get": (os, "open"),
        "head": (pathlib.Path, "lstat"),
        "delete": (pathlib.Path, "unlink"),
        "list": (pathlib.Path, "iterdir"),
        "put": (tempfile, "mkstemp"),
    }

    def __init__(self, monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
        self._monkeypatch = monkeypatch
        self._root = str(root)
        self.modes: dict[str, str | None] = {}
        self.counts: dict[str, int] = dict.fromkeys(self._TARGETS, 0)
        for op, (owner, name) in self._TARGETS.items():
            monkeypatch.setattr(owner, name, self._wrap(op, getattr(owner, name)))

    def _under_root(self, op: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
        if op == "put":
            return str(kwargs.get("dir", "")).startswith(self._root)
        target = args[0] if args else None
        return isinstance(target, (str, os.PathLike)) and os.fspath(target).startswith(self._root)

    def _wrap(self, op: str, original: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if self._under_root(op, args, kwargs):
                self.counts[op] += 1
                mode = self.modes.get(op)
                if mode == "deny":
                    raise PermissionError(errno.EACCES, "permission denied (injected)")
                if mode == "transient":
                    raise OSError(errno.EIO, "input/output error (injected)")
            return original(*args, **kwargs)

        return wrapper

    def set(self, op: str, mode: str | None) -> None:
        self.modes[op] = mode

    def count(self, op: str) -> int:
        return self.counts[op]


class S3Faults:
    def __init__(self, client: ContractS3Client) -> None:
        self._client = client

    def set(self, op: str, mode: str | None) -> None:
        if mode is None:
            self._client.faults.pop(op, None)
        else:
            self._client.faults[op] = mode

    def count(self, op: str) -> int:
        return self._client.operations.count(_OP_NAMES[op])


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


@dataclass
class Harness:
    name: str
    storage: ObjectStorage
    tenant: str
    other_tenant: str
    codes: dict[str, str]
    faults: Any = None
    leftovers: Callable[[set[str]], list[str]] = lambda published: []
    corrupt: Callable[[str], None] | None = None
    reader_closed: Callable[[Any], bool] | None = None
    created: list[tuple[str, str]] = field(default_factory=list)

    @property
    def live(self) -> bool:
        return self.faults is None

    def require_faults(self) -> Any:
        if self.faults is None:
            pytest.skip("fault injection is not available against a live endpoint")
        return self.faults

    def put(
        self,
        payload: bytes,
        *,
        tenant: str | None = None,
        token: str | None = None,
        **kwargs: Any,
    ):
        tenant = tenant or self.tenant
        stored = self.storage.put_stream(
            tenant,
            BytesIO(payload),
            content_type=kwargs.pop("content_type", "application/octet-stream"),
            object_token=token,
            **kwargs,
        )
        self.created.append((tenant, stored.key))
        return stored

    def read(self, key: str, *, tenant: str | None = None) -> bytes:
        with self.storage.open_reader(tenant or self.tenant, key) as reader:
            return reader.read()

    def key_for(self, token: str, *, tenant: str | None = None) -> str:
        return f"{tenant_prefix(tenant or self.tenant)}{token}"


def _local_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    root = tmp_path / "objects"
    storage = LocalObjectStorage(root, max_size_bytes=MAX_OBJECT_BYTES)
    resolved = storage.root

    def leftovers(published: set[str]) -> list[str]:
        keep = set()
        for key in published:
            path = resolved.joinpath(*key.split("/"))
            keep.add(path)
            keep.add(path.with_name(f"{path.name}.metadata.json"))
        return sorted(
            str(p.relative_to(resolved))
            for p in resolved.rglob("*")
            if p.is_file() and p not in keep
        )

    def corrupt(key: str) -> None:
        path = resolved.joinpath(*key.split("/"))
        path.write_bytes(b"tampered-bytes")

    return Harness(
        name="local",
        storage=storage,
        tenant="tenant-a",
        other_tenant="tenant-b",
        codes={
            "get": "local_read_failed",
            "head": "local_stat_failed",
            "delete": "local_delete_failed",
            "list": "local_list_failed",
        },
        faults=LocalFaults(monkeypatch, resolved),
        leftovers=leftovers,
        corrupt=corrupt,
        reader_closed=lambda reader: reader.closed,
    )


def _s3_harness(client: ContractS3Client | None = None) -> Harness:
    client = client or ContractS3Client()
    storage = S3ObjectStorage("contract-bucket", max_size_bytes=MAX_OBJECT_BYTES, client=client)

    def leftovers(published: set[str]) -> list[str]:
        return sorted(key for key in client.objects if key not in published)

    def corrupt(key: str) -> None:
        client.objects[key]["Body"] = b"tampered-bytes"

    return Harness(
        name="s3",
        storage=storage,
        tenant="tenant-a",
        other_tenant="tenant-b",
        codes={
            "get": "s3_read_failed",
            "head": "s3_head_failed",
            "delete": "s3_delete_failed",
            "list": "s3_list_failed",
            "put": "s3_upload_failed",
        },
        faults=S3Faults(client),
        leftovers=leftovers,
        corrupt=corrupt,
        reader_closed=lambda reader: reader.closed,
    )


def _live_harness() -> Harness:
    target = live_s3_target()
    storage = S3ObjectStorage(
        target.bucket,
        max_size_bytes=LIVE_S3_MAX_OBJECT_BYTES,
        endpoint_url=target.endpoint_url,
        region_name=target.region_name,
        access_key_id=target.access_key_id,
        secret_access_key=target.secret_access_key,
        force_path_style=target.endpoint_url is not None,
    )
    run_id = uuid.uuid4().hex[:12]
    return Harness(
        name="live",
        storage=storage,
        tenant=f"{target.tenant_prefix}-{run_id}-a",
        other_tenant=f"{target.tenant_prefix}-{run_id}-b",
        codes={
            "get": "s3_read_failed",
            "head": "s3_head_failed",
            "delete": "s3_delete_failed",
            "list": "s3_list_failed",
            "put": "s3_upload_failed",
        },
        faults=None,
    )


def _minio_storage(
    target: MinioS3Target,
    *,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    bucket: str | None = None,
) -> S3ObjectStorage:
    """``S3ObjectStorage`` on the MinIO container; the app key unless another is given."""
    return S3ObjectStorage(
        bucket or target.bucket,
        max_size_bytes=LIVE_S3_MAX_OBJECT_BYTES,
        endpoint_url=target.endpoint_url,
        region_name=target.region_name,
        access_key_id=access_key_id or target.access_key_id,
        secret_access_key=secret_access_key or target.secret_access_key,
        force_path_style=True,
    )


def _minio_harness() -> Harness:
    target = minio_target()
    run_id = uuid.uuid4().hex[:12]
    return Harness(
        name="minio",
        storage=_minio_storage(target),
        tenant=f"minio-{run_id}-a",
        other_tenant=f"minio-{run_id}-b",
        codes={
            "get": "s3_read_failed",
            "head": "s3_head_failed",
            "delete": "s3_delete_failed",
            "list": "s3_list_failed",
            "put": "s3_upload_failed",
        },
        faults=None,
    )


@pytest.fixture(
    params=[
        pytest.param("local", id="local"),
        pytest.param("s3", id="s3-fake"),
        pytest.param("live", id="s3-live", marks=pytest.mark.live_s3),
        pytest.param("minio", id="s3-minio", marks=pytest.mark.minio_s3),
    ]
)
def harness(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Harness]:
    if request.param == "local":
        yield _local_harness(tmp_path, monkeypatch)
        return
    if request.param == "s3":
        yield _s3_harness()
        return
    # The marker skips already cover these; the checks are defence in depth.
    if request.param == "live":
        reason = live_s3_unavailable_reason()
        if reason is not None:
            pytest.skip(f"NOT_RUN/BLOCKED_EXTERNAL: {reason}")
        build = _live_harness
    else:
        reason = minio_unavailable_reason()
        if reason is not None:
            pytest.skip(f"NOT_RUN: {reason}")
        build = _minio_harness
    previous_levels = quiet_sdk_logging()
    harness = build()
    try:
        yield harness
    finally:
        # Remove exactly the keys this run created (idempotent; never a prefix sweep).
        for tenant, key in harness.created:
            with contextlib.suppress(StorageError):
                harness.storage.delete(tenant, key)
        for logger, level in previous_levels:
            logger.setLevel(level)


# --------------------------------------------------------------------------- #
# Object creation
# --------------------------------------------------------------------------- #


def test_put_stores_bounded_input_with_size_and_digest(harness: Harness) -> None:
    payload = b"contract-payload-" * 8
    stored = harness.put(
        payload, token=TOKEN_A, content_type="audio/wav", original_filename="a.wav"
    )

    assert stored.key == harness.key_for(TOKEN_A)
    assert stored.size_bytes == len(payload)
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    assert stored.content_type == "audio/wav"
    assert harness.storage.exists(harness.tenant, stored.key) is True
    assert harness.read(stored.key) == payload
    listed = list(harness.storage.iter_objects(harness.tenant))
    assert [(entry.key, entry.size_bytes, entry.complete) for entry in listed] == [
        (stored.key, len(payload), True)
    ]
    assert harness.leftovers({stored.key}) == []


def test_empty_input_is_stored_deterministically(harness: Harness) -> None:
    # The storage layer accepts zero bytes; refusing empty media is the
    # validator's job (WP05-T02), not a storage guarantee.
    stored = harness.put(b"", token=TOKEN_A)
    assert stored.size_bytes == 0
    assert stored.sha256 == EMPTY_SHA256
    assert harness.read(stored.key) == b""
    again = harness.put(b"", token=TOKEN_A)
    assert again.key == stored.key and again.sha256 == EMPTY_SHA256


def test_size_boundary_accepts_the_limit_and_refuses_one_more_byte(harness: Harness) -> None:
    limit = 64
    at_limit = harness.put(b"x" * limit, token=TOKEN_A, max_bytes=limit)
    assert at_limit.size_bytes == limit

    puts_before = harness.faults.count("put") if harness.name == "s3" else None
    with pytest.raises(ObjectTooLargeError) as error:
        harness.storage.put_stream(
            harness.tenant,
            iter([b"y" * limit, b"y"]),
            content_type="application/octet-stream",
            max_bytes=limit,
            object_token=TOKEN_B,
        )
    assert error.value.limit_bytes == limit
    assert error.value.observed_bytes == limit + 1
    assert harness.storage.exists(harness.tenant, harness.key_for(TOKEN_B)) is False
    assert harness.leftovers({at_limit.key}) == []
    if puts_before is not None:
        assert harness.faults.count("put") == puts_before  # refused before any network call


def test_configured_maximum_caps_a_larger_caller_limit(harness: Harness) -> None:
    configured = LIVE_S3_MAX_OBJECT_BYTES if harness.live else MAX_OBJECT_BYTES
    with pytest.raises(ObjectTooLargeError) as error:
        harness.storage.put_stream(
            harness.tenant,
            iter([b"z" * 1024] * (configured // 1024 + 1)),
            content_type="application/octet-stream",
            max_bytes=configured * 10,
            object_token=TOKEN_A,
        )
    assert error.value.limit_bytes == configured
    assert harness.storage.exists(harness.tenant, harness.key_for(TOKEN_A)) is False


def test_declared_checksum_mismatch_publishes_nothing(harness: Harness) -> None:
    puts_before = harness.faults.count("put") if harness.name == "s3" else None
    with pytest.raises(ChecksumMismatchError) as error:
        harness.storage.put_stream(
            harness.tenant,
            BytesIO(b"real-bytes"),
            content_type="application/octet-stream",
            expected_sha256="0" * 64,
            object_token=TOKEN_A,
        )
    assert error.value.actual == hashlib.sha256(b"real-bytes").hexdigest()
    assert harness.storage.exists(harness.tenant, harness.key_for(TOKEN_A)) is False
    assert harness.leftovers(set()) == []
    if puts_before is not None:
        assert harness.faults.count("put") == puts_before


def test_token_writes_are_idempotent_on_content_and_conflict_on_different_bytes(
    harness: Harness,
) -> None:
    first = harness.put(b"same-bytes", token=TOKEN_A)
    puts_after_first = harness.faults.count("put") if harness.faults else None

    again = harness.put(b"same-bytes", token=TOKEN_A)
    assert (again.key, again.sha256, again.size_bytes) == (
        first.key,
        first.sha256,
        first.size_bytes,
    )

    with pytest.raises(ObjectConflictError) as conflict:
        harness.put(b"other-bytes", token=TOKEN_A)
    assert conflict.value.key == first.key
    assert harness.read(first.key) == b"same-bytes"  # the existing object is preserved
    if puts_after_first is not None and harness.name == "local":
        assert harness.faults.count("put") >= puts_after_first
    assert harness.leftovers({first.key}) == []


def test_interrupted_input_leaves_no_object_and_no_temporaries(harness: Harness) -> None:
    def interrupted() -> Iterator[bytes]:
        yield b"first-chunk"
        raise StreamInterrupted("client went away")

    with pytest.raises(StreamInterrupted):
        harness.storage.put_stream(
            harness.tenant,
            interrupted(),
            content_type="application/octet-stream",
            object_token=TOKEN_A,
        )
    assert harness.storage.exists(harness.tenant, harness.key_for(TOKEN_A)) is False
    assert harness.leftovers(set()) == []
    # The identity is still free: a complete retry succeeds.
    retried = harness.put(b"complete", token=TOKEN_A)
    assert harness.read(retried.key) == b"complete"


def test_non_byte_chunks_are_refused_without_publication(harness: Harness) -> None:
    with pytest.raises(InvalidStreamError):
        harness.storage.put_stream(
            harness.tenant,
            iter(["text-not-bytes"]),
            content_type="application/octet-stream",
            object_token=TOKEN_A,
        )
    assert harness.storage.exists(harness.tenant, harness.key_for(TOKEN_A)) is False
    assert harness.leftovers(set()) == []


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #


def test_missing_object_is_reported_as_not_found_everywhere(harness: Harness) -> None:
    key = harness.key_for(TOKEN_C)
    assert harness.storage.exists(harness.tenant, key) is False
    with pytest.raises(ObjectNotFoundError):
        harness.storage.open_reader(harness.tenant, key)
    assert harness.storage.delete(harness.tenant, key) is False
    if harness.storage.supports_presigned_access:
        with pytest.raises(ObjectNotFoundError):
            harness.storage.presign_get(harness.tenant, key, expires_in_seconds=60)
    else:
        with pytest.raises(PresignedAccessNotSupportedError):
            harness.storage.presign_get(harness.tenant, key)


def test_reader_streams_in_bounded_chunks_and_releases_the_handle(harness: Harness) -> None:
    payload = bytes(range(256)) * 4
    stored = harness.put(payload, token=TOKEN_A)
    reader = harness.storage.open_reader(harness.tenant, stored.key)
    chunks = []
    try:
        while True:
            chunk = reader.read(100)
            if not chunk:
                break
            assert len(chunk) <= 100
            chunks.append(chunk)
    finally:
        reader.close()
    assert b"".join(chunks) == payload
    if harness.reader_closed is not None:
        assert harness.reader_closed(reader) is True


def test_stored_digest_matches_the_bytes_and_tampering_is_detectable(harness: Harness) -> None:
    stored = harness.put(b"integrity-bytes", token=TOKEN_A)
    assert hashlib.sha256(harness.read(stored.key)).hexdigest() == stored.sha256
    if harness.corrupt is None:
        return
    harness.corrupt(stored.key)
    # The recorded digest is the authority (verify_integrity compares against
    # it); the adapter never "repairs" or hides tampered bytes.
    assert hashlib.sha256(harness.read(stored.key)).hexdigest() != stored.sha256


@pytest.mark.parametrize("mode", ["deny", "transient"])
def test_read_faults_are_not_reported_as_missing(harness: Harness, mode: str) -> None:
    faults = harness.require_faults()
    stored = harness.put(b"present", token=TOKEN_A)
    faults.set("get", mode)
    try:
        with pytest.raises(StorageError) as error:
            harness.storage.open_reader(harness.tenant, stored.key)
    finally:
        faults.set("get", None)
    assert not isinstance(error.value, ObjectNotFoundError)
    assert error.value.code == harness.codes["get"]
    assert harness.read(stored.key) == b"present"  # recovers once the fault clears


@pytest.mark.parametrize("mode", ["deny", "transient"])
def test_metadata_faults_never_answer_false_for_exists(harness: Harness, mode: str) -> None:
    faults = harness.require_faults()
    stored = harness.put(b"present", token=TOKEN_A)
    faults.set("head", mode)
    try:
        with pytest.raises(StorageError) as error:
            harness.storage.exists(harness.tenant, stored.key)
    finally:
        faults.set("head", None)
    assert error.value.code == harness.codes["head"]
    assert harness.storage.exists(harness.tenant, stored.key) is True


# --------------------------------------------------------------------------- #
# Deletion
# --------------------------------------------------------------------------- #


def test_delete_is_idempotent_and_removes_the_listing_entry(harness: Harness) -> None:
    stored = harness.put(b"to-delete", token=TOKEN_A)
    assert harness.storage.delete(harness.tenant, stored.key) is True
    assert harness.storage.delete(harness.tenant, stored.key) is False
    assert harness.storage.exists(harness.tenant, stored.key) is False
    assert list(harness.storage.iter_objects(harness.tenant)) == []
    assert harness.leftovers(set()) == []


@pytest.mark.parametrize("mode", ["deny", "transient"])
def test_delete_faults_preserve_the_object_for_a_retry(harness: Harness, mode: str) -> None:
    faults = harness.require_faults()
    stored = harness.put(b"sticky", token=TOKEN_A)
    faults.set("delete", mode)
    try:
        with pytest.raises(StorageError) as error:
            harness.storage.delete(harness.tenant, stored.key)
    finally:
        faults.set("delete", None)
    assert error.value.code == harness.codes["delete"]
    assert harness.storage.exists(harness.tenant, stored.key) is True
    assert harness.read(stored.key) == b"sticky"
    assert harness.storage.delete(harness.tenant, stored.key) is True  # the retry succeeds


# --------------------------------------------------------------------------- #
# Listing and cleanup
# --------------------------------------------------------------------------- #


def test_listing_covers_the_tenant_prefix_only_and_paginates(harness: Harness) -> None:
    tokens = [f"contract-list-token-{index:04d}" for index in range(5)]
    mine = {
        harness.put(bytes([index]) * (index + 1), token=token).key
        for index, token in enumerate(tokens)
    }
    theirs = {
        harness.put(
            b"other", tenant=harness.other_tenant, token=f"contract-other-token-{index:04d}"
        ).key
        for index in range(2)
    }

    listed = list(harness.storage.iter_objects(harness.tenant))
    assert [entry.key for entry in listed] == sorted(mine)
    assert all(entry.complete for entry in listed)
    assert {entry.key: entry.size_bytes for entry in listed} == {
        harness.key_for(token): index + 1 for index, token in enumerate(tokens)
    }
    assert {entry.key for entry in harness.storage.iter_objects(harness.other_tenant)} == theirs
    if harness.name == "s3":
        assert harness.faults.count("list") >= 3  # five objects at two per page


def test_listing_is_lazy_so_a_budget_bounds_the_backend_work(harness: Harness) -> None:
    faults = harness.require_faults()
    for index in range(5):
        harness.put(b"x", token=f"contract-lazy-token-{index:04d}")
    lists_before = faults.count("list")
    first = list(islice(harness.storage.iter_objects(harness.tenant), 1))
    assert len(first) == 1
    assert faults.count("list") - lists_before == 1  # one page / one directory read


def test_listing_skips_entries_that_are_not_owned_objects(harness: Harness) -> None:
    faults = harness.require_faults()
    stored = harness.put(b"valid", token=TOKEN_A)
    prefix = tenant_prefix(harness.tenant)
    if harness.name == "s3":
        client = faults._client
        client.objects[f"{prefix}short"] = {
            "Body": b"1",
            "Metadata": {},
            "LastModified": datetime.now(UTC),
        }
        client.objects[f"{prefix}nested/{TOKEN_B}"] = {
            "Body": b"2",
            "Metadata": {},
            "LastModified": datetime.now(UTC),
        }
        client.objects[f"{prefix}bad key with spaces 0000000"] = {
            "Body": b"3",
            "Metadata": {},
            "LastModified": datetime.now(UTC),
        }
    else:
        tenant_dir = harness.storage.root / "tenants" / harness.tenant / "objects"
        (tenant_dir / "short").write_bytes(b"1")
        (tenant_dir / "nested-directory-0000000000").mkdir()
        (tenant_dir / TOKEN_B).symlink_to(tenant_dir / TOKEN_A)
    assert [entry.key for entry in harness.storage.iter_objects(harness.tenant)] == [stored.key]


@pytest.mark.parametrize("mode", ["deny", "transient"])
def test_listing_faults_surface_as_storage_errors(harness: Harness, mode: str) -> None:
    faults = harness.require_faults()
    harness.put(b"x", token=TOKEN_A)
    faults.set("list", mode)
    try:
        with pytest.raises(StorageError) as error:
            list(harness.storage.iter_objects(harness.tenant))
    finally:
        faults.set("list", None)
    assert error.value.code == harness.codes["list"]


def test_purge_incomplete_removes_only_stale_leftovers(harness: Harness) -> None:
    faults = harness.require_faults()
    stored = harness.put(b"authoritative", token=TOKEN_A)
    now = datetime.now(UTC)
    if harness.name == "s3":
        ops_before = list(faults._client.operations)
        assert harness.storage.purge_incomplete(harness.tenant, older_than=now) == 0
        assert faults._client.operations == ops_before  # nothing to abort, nothing called
    else:
        tenant_dir = harness.storage.root / "tenants" / harness.tenant / "objects"
        stale_upload = tenant_dir / ".upload-stale"
        stale_upload.write_bytes(b"partial")
        stale_meta = tenant_dir / ".metadata-stale"
        stale_meta.write_bytes(b"{}")
        old = (now - timedelta(hours=2)).timestamp()
        for path in (stale_upload, stale_meta):
            os.utime(path, (old, old))
        fresh_upload = tenant_dir / ".upload-fresh"
        fresh_upload.write_bytes(b"still-writing")
        orphan_sidecar = tenant_dir / f"{TOKEN_B}.metadata.json"
        orphan_sidecar.write_bytes(b"{}")

        removed = harness.storage.purge_incomplete(
            harness.tenant, older_than=now - timedelta(hours=1)
        )
        assert removed == 3
        assert not stale_upload.exists() and not stale_meta.exists() and not orphan_sidecar.exists()
        assert fresh_upload.exists()  # newer than the cut-off: an in-flight write
    assert harness.storage.exists(harness.tenant, stored.key) is True
    assert harness.read(stored.key) == b"authoritative"


# --------------------------------------------------------------------------- #
# Access denial and tenant isolation
# --------------------------------------------------------------------------- #


def test_another_tenants_key_is_refused_before_any_backend_call(harness: Harness) -> None:
    theirs = harness.put(b"theirs", tenant=harness.other_tenant, token=TOKEN_A)
    counts_before = (
        {op: harness.faults.count(op) for op in ("get", "head", "delete")}
        if harness.faults
        else None
    )

    with pytest.raises(InvalidObjectKeyError):
        harness.storage.open_reader(harness.tenant, theirs.key)
    with pytest.raises(InvalidObjectKeyError):
        harness.storage.exists(harness.tenant, theirs.key)
    with pytest.raises(InvalidObjectKeyError):
        harness.storage.delete(harness.tenant, theirs.key)
    with pytest.raises(InvalidObjectKeyError):
        harness.storage.presign_get(harness.tenant, theirs.key, expires_in_seconds=60)

    if counts_before is not None:
        assert {op: harness.faults.count(op) for op in counts_before} == counts_before
    assert harness.read(theirs.key, tenant=harness.other_tenant) == b"theirs"


@pytest.mark.parametrize(
    "forged_key",
    [
        "tenants/tenant-b/objects/" + TOKEN_A,  # another tenant's namespace
        "tenants/tenant-a/objects/../../tenant-b/objects/" + TOKEN_A,  # traversal
        "tenants/tenant-a/objects/" + TOKEN_A + "/" + TOKEN_B,  # extra segment
        "tenants/tenant-a/objects/short",  # token too short
        "tenants/tenant-a/objects/" + TOKEN_A.replace("-", "\\"),  # backslash
        "/tenants/tenant-a/objects/" + TOKEN_A,  # absolute
        "tenants/tenant-a/objects/",  # empty token
        "TENANTS/tenant-a/objects/" + TOKEN_A,  # wrong prefix casing
    ],
)
def test_forged_or_malformed_keys_are_refused(harness: Harness, forged_key: str) -> None:
    tenant = "tenant-a" if not harness.live else harness.tenant
    if harness.live:
        forged_key = forged_key.replace("tenant-a", harness.tenant).replace(
            "tenant-b", harness.other_tenant
        )
    for operation in (harness.storage.exists, harness.storage.open_reader, harness.storage.delete):
        with pytest.raises(InvalidObjectKeyError):
            operation(tenant, forged_key)
    with pytest.raises(InvalidObjectKeyError):
        harness.storage.presign_get(tenant, forged_key, expires_in_seconds=60)


@pytest.mark.parametrize(
    "bad_tenant", ["", "../tenant-a", "tenant/a", "-leading", "a" * 129, ".hidden"]
)
def test_invalid_tenant_ids_never_reach_listing_purge_or_write(
    harness: Harness, bad_tenant: str
) -> None:
    counts_before = (
        {op: harness.faults.count(op) for op in ("put", "list")} if harness.faults else None
    )
    with pytest.raises(InvalidTenantError):
        list(harness.storage.iter_objects(bad_tenant))
    with pytest.raises(InvalidTenantError):
        harness.storage.purge_incomplete(bad_tenant, older_than=datetime.now(UTC))
    with pytest.raises(InvalidTenantError):
        harness.storage.put_stream(
            bad_tenant, BytesIO(b"x"), content_type="application/octet-stream"
        )
    if counts_before is not None:
        assert {op: harness.faults.count(op) for op in counts_before} == counts_before


def test_bad_object_tokens_are_refused_before_writing(harness: Harness) -> None:
    for bad_token in (
        "short",
        "has/slash-0000000000000",
        "../" + TOKEN_A,
        TOKEN_A + " ",
        "x" * 129,
    ):
        with pytest.raises(InvalidObjectKeyError):
            harness.storage.put_stream(
                harness.tenant,
                BytesIO(b"x"),
                content_type="application/octet-stream",
                object_token=bad_token,
            )
    assert harness.leftovers(set()) == []


def test_provider_denies_the_write_and_nothing_authoritative_remains(harness: Harness) -> None:
    faults = harness.require_faults()
    faults.set("put", "deny")
    try:
        with pytest.raises(Exception) as error:
            harness.storage.put_stream(
                harness.tenant,
                BytesIO(b"denied"),
                content_type="application/octet-stream",
                object_token=TOKEN_A,
            )
    finally:
        faults.set("put", None)
    if harness.name == "s3":
        assert isinstance(error.value, StorageError)
        assert error.value.code == harness.codes["put"]
        assert faults.count("delete") == 0  # a caller-chosen key is never cleaned up blindly
    else:
        assert isinstance(error.value, PermissionError)
    assert harness.storage.exists(harness.tenant, harness.key_for(TOKEN_A)) is False
    assert harness.leftovers(set()) == []


# --------------------------------------------------------------------------- #
# S3-specific: classification the shared suite cannot express
# --------------------------------------------------------------------------- #


def test_s3_missing_bucket_is_never_reported_as_a_missing_object() -> None:
    client = ContractS3Client()
    harness = _s3_harness(client)
    stored = harness.put(b"x", token=TOKEN_A)
    for op in ("get", "head", "list", "put", "delete"):
        client.faults[op] = "bucket_missing"
    try:
        with pytest.raises(StorageError) as head_error:
            harness.storage.exists(harness.tenant, stored.key)
        with pytest.raises(StorageError) as get_error:
            harness.storage.open_reader(harness.tenant, stored.key)
        with pytest.raises(StorageError) as list_error:
            list(harness.storage.iter_objects(harness.tenant))
        with pytest.raises(StorageError) as put_error:
            harness.put(b"y", token=TOKEN_B)
    finally:
        client.faults.clear()
    for error in (head_error, get_error, list_error, put_error):
        assert not isinstance(error.value, ObjectNotFoundError)
        assert error.value.code == "s3_bucket_unavailable"
    assert client.objects.get(harness.key_for(TOKEN_B)) is None


def test_s3_bodiless_head_404_confirms_the_bucket_before_answering_missing() -> None:
    # MinIO answers HEAD object on a missing bucket with a bodiless 404, the
    # same shape as a missing object; the adapter must confirm the bucket.
    missing = ContractS3Client(bucket_exists=False)
    missing_harness = _s3_harness(missing)
    key = missing_harness.key_for(TOKEN_A)

    with pytest.raises(StorageError) as head_error:
        missing_harness.storage.exists(missing_harness.tenant, key)
    with pytest.raises(StorageError) as delete_error:
        missing_harness.storage.delete(missing_harness.tenant, key)
    for error in (head_error, delete_error):
        assert not isinstance(error.value, ObjectNotFoundError)
        assert error.value.code == "s3_bucket_unavailable"
    assert missing.operations == [
        "HeadObject",
        "HeadBucket",
        "HeadObject",
        "HeadBucket",
    ]  # delete() goes through exists(): one HEAD pair per call

    # Allow-control: with the bucket present, a key that was never written is
    # only a missing object.
    present = ContractS3Client()
    present_harness = _s3_harness(present)
    control_key = present_harness.key_for(TOKEN_A)
    assert present_harness.storage.exists(present_harness.tenant, control_key) is False
    assert present_harness.storage.delete(present_harness.tenant, control_key) is False


def test_s3_incomplete_get_response_is_an_invalid_response_not_missing() -> None:
    client = ContractS3Client()
    harness = _s3_harness(client)
    stored = harness.put(b"x", token=TOKEN_A)
    client.faults["get"] = "incomplete"
    with pytest.raises(StorageError) as error:
        harness.storage.open_reader(harness.tenant, stored.key)
    assert error.value.code == "invalid_s3_response"


def test_s3_listing_tolerates_an_empty_truncated_page() -> None:
    client = ContractS3Client(empty_first_page=True)
    harness = _s3_harness(client)
    keys = {harness.put(b"x", token=f"contract-page-token-{index:04d}").key for index in range(3)}
    assert {entry.key for entry in harness.storage.iter_objects(harness.tenant)} == keys
    assert client.operations.count("ListObjectsV2") == 3  # empty page + two data pages


def test_s3_reader_body_is_closed_by_the_caller_contract() -> None:
    client = ContractS3Client()
    harness = _s3_harness(client)
    stored = harness.put(b"body", token=TOKEN_A)
    with harness.storage.open_reader(harness.tenant, stored.key) as reader:
        assert reader.read() == b"body"
    assert all(body.closed for body in client.bodies)


# --------------------------------------------------------------------------- #
# Proof of the bounded single-request upload on the real SDK path
# --------------------------------------------------------------------------- #


@dataclass
class SdkProbe:
    """A real boto3 client whose HTTP layer is replaced by ``Stubber``.

    ``operations`` records the name of every API operation the adapter
    issues (one entry per API call; botocore's own retry of a single request
    would not add entries, and no retry is disabled here).  An operation the
    test did not stub raises ``UnStubbedResponseError`` — so a hidden
    ``CreateMultipartUpload`` would fail loudly.
    """

    client: Any
    stubber: Any
    operations: list[str]
    params: dict[str, list[dict[str, Any]]]


@pytest.fixture
def sdk_probe() -> Iterator[SdkProbe]:
    boto3 = pytest.importorskip("boto3")
    from botocore.stub import Stubber

    client = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="contract-test-key",
        aws_secret_access_key="contract-test-secret",  # noqa: S106 - fake, never sent
    )
    operations: list[str] = []
    recorded: dict[str, list[dict[str, Any]]] = {}

    def record(params: dict[str, Any], model: Any, **_: Any) -> None:
        operations.append(model.name)
        recorded.setdefault(model.name, []).append({k: v for k, v in params.items() if k != "Body"})

    client.meta.events.register("provide-client-params.s3", record)
    stubber = Stubber(client)
    stubber.activate()
    try:
        yield SdkProbe(client=client, stubber=stubber, operations=operations, params=recorded)
        stubber.assert_no_pending_responses()
    finally:
        stubber.deactivate()


def _probe_storage(probe: SdkProbe, **kwargs: Any) -> S3ObjectStorage:
    return S3ObjectStorage(
        "contract-bucket", max_size_bytes=MAX_OBJECT_BYTES, client=probe.client, **kwargs
    )


def test_s3_adapter_source_never_reaches_for_transfer_helpers() -> None:
    source = inspect.getsource(s3_module)
    for forbidden in (
        "upload_fileobj",
        "upload_file",
        "TransferConfig",
        "s3transfer",
        "create_multipart_upload",
    ):
        assert forbidden not in source, forbidden
    assert "put_object" in source


def test_s3_generated_key_upload_is_exactly_one_put_object(sdk_probe: SdkProbe) -> None:
    sdk_probe.stubber.add_response("put_object", {"ETag": '"x"'})
    storage = _probe_storage(sdk_probe, key_factory=lambda: TOKEN_A, native_checksum=True)
    payload = b"single-request-" * 10

    stored = storage.put_stream("tenant-a", BytesIO(payload), content_type="audio/wav")

    assert sdk_probe.operations == ["PutObject"]
    assert not _MULTIPART_OPERATIONS & set(sdk_probe.operations)
    sent = sdk_probe.params["PutObject"][0]
    assert sent["ContentLength"] == len(payload) == stored.size_bytes
    assert sent["Key"] == stored.key
    assert sent["Metadata"]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert "IfNoneMatch" not in sent  # generated keys are fresh, not conditional
    assert sent["ChecksumSHA256"]  # the SDK-verified digest, not the ETag
    assert "ACL" not in sent


def test_s3_unknown_length_stream_is_spooled_and_sent_with_the_exact_length(
    sdk_probe: SdkProbe,
) -> None:
    sdk_probe.stubber.add_response("put_object", {"ETag": '"x"'})
    # Spool threshold far below the payload: the body rolls over to a
    # temporary file, never an in-memory copy of unbounded size.
    storage = _probe_storage(sdk_probe, key_factory=lambda: TOKEN_A, spool_memory_bytes=16)

    def chunks() -> Iterator[bytes]:
        for index in range(7):
            yield bytes([index]) * 100

    stored = storage.put_stream("tenant-a", chunks(), content_type="application/octet-stream")

    assert sdk_probe.operations == ["PutObject"]
    assert sdk_probe.params["PutObject"][0]["ContentLength"] == 700 == stored.size_bytes


def test_s3_oversized_input_issues_no_request_at_all(sdk_probe: SdkProbe) -> None:
    storage = _probe_storage(sdk_probe, key_factory=lambda: TOKEN_A)
    with pytest.raises(ObjectTooLargeError):
        storage.put_stream(
            "tenant-a",
            iter([b"a" * MAX_OBJECT_BYTES, b"b"]),
            content_type="application/octet-stream",
        )
    assert sdk_probe.operations == []


def test_s3_token_write_is_a_conditional_put_and_a_conflict_preserves_the_existing_object(
    sdk_probe: SdkProbe,
) -> None:
    sdk_probe.stubber.add_client_error(
        "put_object", service_error_code="PreconditionFailed", http_status_code=412
    )
    sdk_probe.stubber.add_response(
        "head_object",
        {"ContentLength": 5, "Metadata": {"sha256": "f" * 64, "tenant-id": "tenant-a"}},
    )
    storage = _probe_storage(sdk_probe)

    with pytest.raises(ObjectConflictError):
        storage.put_stream(
            "tenant-a", BytesIO(b"mine!"), content_type="application/json", object_token=TOKEN_A
        )

    assert sdk_probe.operations == ["PutObject", "HeadObject"]  # compare, never delete/overwrite
    assert sdk_probe.params["PutObject"][0]["IfNoneMatch"] == "*"


def test_s3_failed_generated_upload_cleans_up_while_a_token_upload_never_deletes(
    sdk_probe: SdkProbe,
) -> None:
    sdk_probe.stubber.add_client_error(
        "put_object", service_error_code="InternalError", http_status_code=500
    )
    sdk_probe.stubber.add_response("delete_object", {})
    sdk_probe.stubber.add_client_error(
        "put_object", service_error_code="InternalError", http_status_code=500
    )
    storage = _probe_storage(sdk_probe, key_factory=lambda: TOKEN_A)

    with pytest.raises(StorageError) as generated:
        storage.put_stream("tenant-a", BytesIO(b"x"), content_type="application/octet-stream")
    assert generated.value.code == "s3_upload_failed"
    assert sdk_probe.operations == ["PutObject", "DeleteObject"]

    with pytest.raises(StorageError) as token:
        storage.put_stream(
            "tenant-a", BytesIO(b"x"), content_type="application/octet-stream", object_token=TOKEN_B
        )
    assert token.value.code == "s3_upload_failed"
    assert sdk_probe.operations == ["PutObject", "DeleteObject", "PutObject"]
    assert not _MULTIPART_OPERATIONS & set(sdk_probe.operations)


def test_s3_real_client_errors_are_classified_distinctly(sdk_probe: SdkProbe) -> None:
    key = f"{tenant_prefix('tenant-a')}{TOKEN_A}"
    storage = _probe_storage(sdk_probe)
    stub = sdk_probe.stubber

    stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
    with pytest.raises(ObjectNotFoundError):
        storage.open_reader("tenant-a", key)

    stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
    stub.add_response("head_bucket", {})
    assert storage.exists("tenant-a", key) is False

    stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
    stub.add_client_error("head_bucket", service_error_code="404", http_status_code=404)
    with pytest.raises(StorageError) as missing_bucket:
        storage.exists("tenant-a", key)
    assert missing_bucket.value.code == "s3_bucket_unavailable"

    stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
    stub.add_client_error("head_bucket", service_error_code="AccessDenied", http_status_code=403)
    with pytest.raises(StorageError) as denied_probe:
        storage.exists("tenant-a", key)
    assert denied_probe.value.code == "s3_head_failed"

    stub.add_client_error("head_object", service_error_code="AccessDenied", http_status_code=403)
    with pytest.raises(StorageError) as denied:
        storage.exists("tenant-a", key)
    assert denied.value.code == "s3_head_failed"

    stub.add_client_error("get_object", service_error_code="AccessDenied", http_status_code=403)
    with pytest.raises(StorageError) as denied_read:
        storage.open_reader("tenant-a", key)
    assert denied_read.value.code == "s3_read_failed"

    stub.add_client_error("head_object", service_error_code="NoSuchBucket", http_status_code=404)
    with pytest.raises(StorageError) as bucket:
        storage.exists("tenant-a", key)
    assert bucket.value.code == "s3_bucket_unavailable"

    stub.add_client_error(
        "list_objects_v2", service_error_code="AccessDenied", http_status_code=403
    )
    with pytest.raises(StorageError) as denied_list:
        list(storage.iter_objects("tenant-a"))
    assert denied_list.value.code == "s3_list_failed"

    assert sdk_probe.operations == [
        "GetObject",
        "HeadObject",
        "HeadBucket",
        "HeadObject",
        "HeadBucket",
        "HeadObject",
        "HeadBucket",
        "HeadObject",
        "GetObject",
        "HeadObject",
        "ListObjectsV2",
    ]


# --------------------------------------------------------------------------- #
# MinIO (HOM-36): real denial, presign and multipart decisions
# --------------------------------------------------------------------------- #

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MINIO_SCRIPT = _REPO_ROOT / "infra" / "ci" / "minio-contract-target.sh"
_ONPREM_COMPOSE = _REPO_ROOT / "infra" / "compose" / "compose.onprem.yaml"
# Plain HTTP for the unsigned, presigned, expired and tampered requests; never
# through a proxy from the environment (the target is a loopback container).
_DIRECT_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_get(url: str) -> tuple[int, bytes]:
    try:
        with _DIRECT_HTTP.open(url, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read()


def _s3_error_code(body: bytes) -> str | None:
    match = re.search(rb"<Code>([^<]+)</Code>", body)
    return match.group(1).decode() if match else None


def _s3_error_message(body: bytes) -> str:
    match = re.search(rb"<Message>([^<]*)</Message>", body)
    return match.group(1).decode() if match else ""


def _denial(error: BaseException) -> tuple[str, int | None]:
    """Code and HTTP status of the provider ``ClientError`` behind ``error``."""
    current: BaseException | None = error
    while current is not None and not isinstance(current, ClientError):
        current = current.__cause__
    assert isinstance(current, ClientError), f"no ClientError behind {error!r}"
    return (
        str(current.response.get("Error", {}).get("Code", "")),
        current.response.get("ResponseMetadata", {}).get("HTTPStatusCode"),
    )


def _raw_client(target: MinioS3Target, access_key_id: str, secret_access_key: str) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=target.endpoint_url,
        region_name=target.region_name,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            ignore_configured_endpoint_urls=True,
        ),
    )


def _multipart_uploads(client: Any, bucket: str, prefix: str) -> list[tuple[str, str]]:
    response = client.list_multipart_uploads(Bucket=bucket, Prefix=prefix)
    return [(upload["Key"], upload["UploadId"]) for upload in response.get("Uploads", [])]


@dataclass
class MinioRun:
    target: MinioS3Target
    app: S3ObjectStorage
    denied: S3ObjectStorage
    app_raw: Any
    denied_raw: Any
    tenant: str
    created: list[str] = field(default_factory=list)

    @staticmethod
    def token() -> str:
        return f"minio-case-{uuid.uuid4().hex}"

    def key_for(self, token: str) -> str:
        return f"{tenant_prefix(self.tenant)}{token}"

    def put(self, payload: bytes, *, token: str | None = None) -> StoredObject:
        stored = self.app.put_stream(
            self.tenant,
            BytesIO(payload),
            content_type="application/octet-stream",
            object_token=token,
        )
        self.created.append(stored.key)
        return stored

    def read(self, key: str) -> bytes:
        with self.app.open_reader(self.tenant, key) as reader:
            return reader.read()

    def listed(self) -> list[str]:
        return [entry.key for entry in self.app.iter_objects(self.tenant)]

    def object_url(self, key: str) -> str:
        return f"{self.target.endpoint_url.rstrip('/')}/{self.target.bucket}/{key}"


@pytest.fixture
def minio_run() -> Iterator[MinioRun]:
    reason = minio_unavailable_reason()
    if reason is not None:  # defence in depth: the marker skip already covers this
        pytest.skip(f"NOT_RUN: {reason}")
    previous_levels = quiet_sdk_logging()
    target = minio_target()
    run = MinioRun(
        target=target,
        app=_minio_storage(target),
        denied=_minio_storage(
            target,
            access_key_id=target.denied_access_key_id,
            secret_access_key=target.denied_secret_access_key,
        ),
        app_raw=_raw_client(target, target.access_key_id, target.secret_access_key),
        denied_raw=_raw_client(
            target, target.denied_access_key_id, target.denied_secret_access_key
        ),
        tenant=f"minio-{uuid.uuid4().hex[:12]}-a",
    )
    try:
        yield run
    finally:
        # Remove exactly the keys this run created (idempotent; never a prefix sweep).
        for key in run.created:
            with contextlib.suppress(StorageError):
                run.app.delete(run.tenant, key)
        for logger, level in previous_levels:
            logger.setLevel(level)


@pytest.mark.minio_s3
def test_minio_denied_key_cannot_write_and_nothing_is_published(minio_run: MinioRun) -> None:
    run = minio_run
    listed_before = run.listed()
    token = run.token()
    generated_token = run.token()
    key, generated_key = run.key_for(token), run.key_for(generated_token)
    run.created.extend([key, generated_key])  # removed afterwards even if a denial fails open

    with pytest.raises(StorageError) as token_error:
        run.denied.put_stream(
            run.tenant,
            BytesIO(b"denied-write"),
            content_type="application/octet-stream",
            object_token=token,
        )
    assert token_error.value.code == "s3_upload_failed"
    assert _denial(token_error.value) == ("AccessDenied", 403)

    # A generated key is cleaned up after a failed PUT; the cleanup DELETE is
    # denied too, so the adapter reports that it could not confirm it.
    denied_generated = S3ObjectStorage(
        run.target.bucket,
        max_size_bytes=LIVE_S3_MAX_OBJECT_BYTES,
        endpoint_url=run.target.endpoint_url,
        region_name=run.target.region_name,
        access_key_id=run.target.denied_access_key_id,
        secret_access_key=run.target.denied_secret_access_key,
        force_path_style=True,
        key_factory=lambda: generated_token,
    )
    with pytest.raises(StorageError) as generated_error:
        denied_generated.put_stream(
            run.tenant, BytesIO(b"denied-generated"), content_type="application/octet-stream"
        )
    assert generated_error.value.code == "s3_upload_cleanup_failed"
    assert _denial(generated_error.value) == ("AccessDenied", 403)

    assert run.app.exists(run.tenant, key) is False
    assert run.app.exists(run.tenant, generated_key) is False
    assert run.listed() == listed_before

    # Allow-control: the same writes with the app key succeed.
    assert run.put(b"denied-write", token=token).key == key
    allowed_generated = S3ObjectStorage(
        run.target.bucket,
        max_size_bytes=LIVE_S3_MAX_OBJECT_BYTES,
        endpoint_url=run.target.endpoint_url,
        region_name=run.target.region_name,
        access_key_id=run.target.access_key_id,
        secret_access_key=run.target.secret_access_key,
        force_path_style=True,
        key_factory=lambda: generated_token,
    )
    stored = allowed_generated.put_stream(
        run.tenant, BytesIO(b"denied-generated"), content_type="application/octet-stream"
    )
    assert stored.key == generated_key
    assert run.read(key) == b"denied-write"
    assert run.read(generated_key) == b"denied-generated"
    assert run.listed() == sorted([*listed_before, key, generated_key])


@pytest.mark.minio_s3
def test_minio_denied_key_cannot_read_head_delete_or_list(minio_run: MinioRun) -> None:
    run = minio_run
    stored = run.put(b"owned-by-app", token=run.token())

    with pytest.raises(StorageError) as read_error:
        run.denied.open_reader(run.tenant, stored.key)
    with pytest.raises(StorageError) as head_error:
        run.denied.exists(run.tenant, stored.key)
    # delete() checks exists() first, so the denied HEAD answers before any DELETE.
    with pytest.raises(StorageError) as delete_error:
        run.denied.delete(run.tenant, stored.key)
    with pytest.raises(StorageError) as list_error:
        list(run.denied.iter_objects(run.tenant))

    for error in (read_error, head_error, delete_error, list_error):
        assert not isinstance(error.value, ObjectNotFoundError)
    assert read_error.value.code == "s3_read_failed"
    assert _denial(read_error.value) == ("AccessDenied", 403)
    assert head_error.value.code == "s3_head_failed"
    assert _denial(head_error.value)[1] == 403  # HEAD has no body, so no error code
    assert delete_error.value.code == "s3_head_failed"
    assert _denial(delete_error.value)[1] == 403
    assert list_error.value.code == "s3_list_failed"
    assert _denial(list_error.value) == ("AccessDenied", 403)

    # The DELETE itself is denied too, not only the HEAD in front of it.
    with pytest.raises(ClientError) as raw_delete:
        run.denied_raw.delete_object(Bucket=run.target.bucket, Key=stored.key)
    assert _denial(raw_delete.value) == ("AccessDenied", 403)

    # Allow-control: the app key still reads the same object with the same bytes.
    assert run.app.exists(run.tenant, stored.key) is True
    assert run.read(stored.key) == b"owned-by-app"
    assert stored.key in run.listed()


@pytest.mark.minio_s3
def test_minio_anonymous_get_is_denied_and_a_presigned_get_is_served(
    minio_run: MinioRun,
) -> None:
    run = minio_run
    payload = b"private-object-bytes"
    stored = run.put(payload, token=run.token())

    status, body = _http_get(run.object_url(stored.key))
    assert (status, _s3_error_code(body)) == (403, "AccessDenied")

    # Allow-control: a presigned URL for the same object, within its TTL.
    presigned = run.app.presign_get(run.tenant, stored.key, expires_in_seconds=60)
    assert presigned.url.startswith(run.object_url(stored.key) + "?")
    assert _http_get(presigned.url) == (200, payload)


@pytest.mark.minio_s3
def test_minio_expired_and_tampered_presigned_urls_are_refused(minio_run: MinioRun) -> None:
    run = minio_run
    payload = b"presigned-bytes"
    stored = run.put(payload, token=run.token())

    short = run.app.presign_get(run.tenant, stored.key, expires_in_seconds=1)
    valid = run.app.presign_get(run.tenant, stored.key, expires_in_seconds=60)
    signature = re.search(r"X-Amz-Signature=([0-9a-f]+)", valid.url)
    assert signature is not None
    last = signature.group(1)[-1]
    tampered = valid.url.replace(
        f"X-Amz-Signature={signature.group(1)}",
        f"X-Amz-Signature={signature.group(1)[:-1]}{'1' if last == '0' else '0'}",
    )
    assert tampered != valid.url

    time.sleep(3)  # well past the one-second TTL (X-Amz-Date has second precision)
    status, body = _http_get(short.url)
    assert (status, _s3_error_code(body)) == (403, "AccessDenied")
    # The denial is the expiry itself, not a signature or policy failure.
    assert _s3_error_message(body) == "Request has expired"
    status, body = _http_get(tampered)
    assert (status, _s3_error_code(body)) == (403, "SignatureDoesNotMatch")

    # Allow-control: an untouched URL and a fresh one are both served.
    assert _http_get(valid.url) == (200, payload)
    fresh = run.app.presign_get(run.tenant, stored.key, expires_in_seconds=60)
    assert _http_get(fresh.url) == (200, payload)


@pytest.mark.minio_s3
def test_minio_app_policy_is_least_privilege(minio_run: MinioRun) -> None:
    run = minio_run
    stored = run.put(b"least-privilege", token=run.token())
    bucket = run.target.bucket
    other = f"{bucket}-other"  # never created

    with pytest.raises(ClientError) as create_error:
        run.app_raw.create_bucket(Bucket=f"{bucket}-created-{uuid.uuid4().hex[:8]}")
    assert _denial(create_error.value) == ("AccessDenied", 403)

    public_read = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": ["*"]},
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/*"],
            }
        ],
    }
    with pytest.raises(ClientError) as policy_error:
        run.app_raw.put_bucket_policy(Bucket=bucket, Policy=json.dumps(public_read))
    assert _denial(policy_error.value) == ("AccessDenied", 403)

    second_bucket_calls = {
        "get": lambda: run.app_raw.get_object(Bucket=other, Key=stored.key),
        "put": lambda: run.app_raw.put_object(Bucket=other, Key=stored.key, Body=b"x"),
        "list": lambda: run.app_raw.list_objects_v2(Bucket=other),
    }
    for name, call in second_bucket_calls.items():
        with pytest.raises(ClientError) as other_error:
            call()
        assert _denial(other_error.value)[0] in {"AccessDenied", "NoSuchBucket"}, name

    # The refused policy left the bucket private.
    status, body = _http_get(run.object_url(stored.key))
    assert (status, _s3_error_code(body)) == (403, "AccessDenied")

    # Allow-control: the app key lists its own bucket.
    listing = run.app_raw.list_objects_v2(Bucket=bucket, Prefix=tenant_prefix(run.tenant))
    assert [entry["Key"] for entry in listing.get("Contents", [])] == [stored.key]


@pytest.mark.minio_s3
def test_minio_missing_bucket_is_unavailable_for_reads_and_listing(minio_run: MinioRun) -> None:
    run = minio_run
    target = run.target
    probe = _minio_storage(
        target,
        access_key_id=target.probe_access_key_id,
        secret_access_key=target.probe_secret_access_key,
        bucket=target.missing_bucket,
    )
    key = run.key_for(run.token())

    with pytest.raises(StorageError) as read_error:
        probe.open_reader(run.tenant, key)
    with pytest.raises(StorageError) as list_error:
        list(probe.iter_objects(run.tenant))
    for error in (read_error, list_error):
        assert not isinstance(error.value, ObjectNotFoundError)
        assert error.value.code == "s3_bucket_unavailable"
        assert _denial(error.value) == ("NoSuchBucket", 404)

    # Allow-control: the real bucket answers normally with the app key.
    stored = run.put(b"bucket-present", token=run.token())
    assert run.app.exists(run.tenant, stored.key) is True
    assert run.app.exists(run.tenant, key) is False


@pytest.mark.minio_s3
def test_minio_missing_bucket_is_never_reported_as_a_missing_object(minio_run: MinioRun) -> None:
    run = minio_run
    target = run.target
    probe = _minio_storage(
        target,
        access_key_id=target.probe_access_key_id,
        secret_access_key=target.probe_secret_access_key,
        bucket=target.missing_bucket,
    )
    key = run.key_for(run.token())

    with pytest.raises(StorageError) as head_error:
        probe.exists(run.tenant, key)
    with pytest.raises(StorageError) as delete_error:
        probe.delete(run.tenant, key)
    for error in (head_error, delete_error):
        assert not isinstance(error.value, ObjectNotFoundError)
        assert error.value.code == "s3_bucket_unavailable"

    # Allow-control: on the real bucket the app key still answers "missing"
    # for a key that was never written.
    assert run.app.exists(run.tenant, key) is False
    assert run.app.delete(run.tenant, key) is False


@pytest.mark.minio_s3
def test_minio_bucket_outside_the_app_policy_is_denied_not_missing(minio_run: MinioRun) -> None:
    run = minio_run
    outside = _minio_storage(run.target, bucket=f"{run.target.bucket}-other")
    key = run.key_for(run.token())

    with pytest.raises(StorageError) as head_error:
        outside.exists(run.tenant, key)
    with pytest.raises(StorageError) as read_error:
        outside.open_reader(run.tenant, key)
    for error in (head_error, read_error):
        assert not isinstance(error.value, ObjectNotFoundError)
    assert head_error.value.code == "s3_head_failed"
    assert _denial(head_error.value)[1] == 403
    assert read_error.value.code == "s3_read_failed"
    assert _denial(read_error.value) == ("AccessDenied", 403)

    # Allow-control: the configured bucket answers normally with the same key.
    stored = run.put(b"policy-bucket", token=run.token())
    assert run.app.exists(run.tenant, stored.key) is True
    assert run.read(stored.key) == b"policy-bucket"


def _assert_not_an_object(run: MinioRun, key: str) -> None:
    assert run.app.exists(run.tenant, key) is False
    with pytest.raises(ObjectNotFoundError):
        run.app.open_reader(run.tenant, key)
    assert key not in run.listed()


@pytest.mark.minio_s3
def test_minio_aborted_multipart_upload_leaves_no_accessible_orphan(minio_run: MinioRun) -> None:
    run = minio_run
    bucket = run.target.bucket
    key = run.key_for(run.token())
    upload_id = run.app_raw.create_multipart_upload(
        Bucket=bucket, Key=key, ContentType="application/octet-stream"
    )["UploadId"]
    aborted = False
    try:
        run.app_raw.upload_part(
            Bucket=bucket, Key=key, PartNumber=1, UploadId=upload_id, Body=b"part-one" * 128
        )
        # Control: the upload is real and visible to ListMultipartUploads.
        assert _multipart_uploads(run.app_raw, bucket, key) == [(key, upload_id)]
        _assert_not_an_object(run, key)

        run.app_raw.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        aborted = True

        _assert_not_an_object(run, key)
        assert _multipart_uploads(run.app_raw, bucket, key) == []
        assert _multipart_uploads(run.app_raw, bucket, tenant_prefix(run.tenant)) == []
        with pytest.raises(ClientError) as parts_error:
            run.app_raw.list_parts(Bucket=bucket, Key=key, UploadId=upload_id)
        assert _denial(parts_error.value)[0] == "NoSuchUpload"
    finally:
        if not aborted:
            with contextlib.suppress(ClientError):
                run.app_raw.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)


@pytest.mark.minio_s3
def test_minio_adapter_never_starts_a_multipart_upload(minio_run: MinioRun) -> None:
    run = minio_run
    storage = _minio_storage(run.target)
    operations: list[str] = []

    def record(params: dict[str, Any], model: Any, **_: Any) -> None:
        operations.append(model.name)

    storage._client.meta.events.register("provide-client-params.s3", record)

    generated = storage.put_stream(
        run.tenant, BytesIO(b"generated-" * 64), content_type="application/octet-stream"
    )
    run.created.append(generated.key)
    token_key = run.key_for(run.token())
    run.created.append(token_key)
    stored = storage.put_stream(
        run.tenant,
        BytesIO(b"token-" * 64),
        content_type="application/octet-stream",
        object_token=token_key.rsplit("/", 1)[1],
    )
    assert stored.key == token_key

    oversized_key = run.key_for(run.token())
    with pytest.raises(ObjectTooLargeError):
        storage.put_stream(
            run.tenant,
            iter([b"o" * 1024] * (LIVE_S3_MAX_OBJECT_BYTES // 1024 + 1)),
            content_type="application/octet-stream",
            object_token=oversized_key.rsplit("/", 1)[1],
        )

    def interrupted() -> Iterator[bytes]:
        yield b"first-chunk"
        raise StreamInterrupted("client went away")

    interrupted_key = run.key_for(run.token())
    with pytest.raises(StreamInterrupted):
        storage.put_stream(
            run.tenant,
            interrupted(),
            content_type="application/octet-stream",
            object_token=interrupted_key.rsplit("/", 1)[1],
        )

    # Control: the recorder sees the adapter's real requests.
    assert operations.count("PutObject") == 2
    assert not _MULTIPART_OPERATIONS & set(operations)
    for key in (generated.key, token_key, oversized_key, interrupted_key):
        assert _multipart_uploads(run.app_raw, run.target.bucket, key) == []
    assert _multipart_uploads(run.app_raw, run.target.bucket, tenant_prefix(run.tenant)) == []
    assert storage.purge_incomplete(run.tenant, older_than=datetime.now(UTC)) == 0
    assert run.app.exists(run.tenant, oversized_key) is False
    assert run.app.exists(run.tenant, interrupted_key) is False


@pytest.mark.minio_s3
def test_minio_delete_on_the_versioned_bucket_hides_the_object(minio_run: MinioRun) -> None:
    # The script checks with the root key that versioning is Enabled and that
    # anonymous access is none before it writes the env file; no test
    # credential may read GetBucketVersioning.
    run = minio_run
    stored = run.put(b"versioned", token=run.token())
    assert run.app.exists(run.tenant, stored.key) is True  # control

    assert run.app.delete(run.tenant, stored.key) is True
    assert run.app.exists(run.tenant, stored.key) is False
    with pytest.raises(ObjectNotFoundError):
        run.app.open_reader(run.tenant, stored.key)
    assert stored.key not in run.listed()


# --------------------------------------------------------------------------- #
# MinIO target: static guards that need no container
# --------------------------------------------------------------------------- #

_POLICY_STATEMENT_RE = re.compile(r'"Effect":"(\w+)","Action":\[([^\]]*)\],"Resource":\[([^\]]*)\]')
# The bucket is spliced into the single-quoted policy text by the shell:
# '"$$MINIO_BUCKET"' in Compose, '"$1"' in the script.
_SHELL_BUCKET_SPLICE_RE = re.compile(r"'\"\$\$?\{?\w+\}?\"'")


def _policy_statements(text: str) -> list[tuple[str, frozenset[str], frozenset[str]]]:
    text = _SHELL_BUCKET_SPLICE_RE.sub("<bucket>", text)
    return sorted(
        (
            effect,
            frozenset(re.findall(r'"([^"]+)"', actions)),
            frozenset(re.findall(r'"([^"]+)"', resources)),
        )
        for effect, actions, resources in _POLICY_STATEMENT_RE.findall(text)
    )


def _section(text: str, pattern: str) -> str:
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    assert match is not None, pattern
    return match.group(1)


def test_minio_script_app_policy_matches_the_onprem_compose_bootstrap() -> None:
    compose = _section(
        _ONPREM_COMPOSE.read_text(encoding="utf-8"), r"^  minio-bootstrap:\n(.*?)^  \S"
    )
    script = _section(_MINIO_SCRIPT.read_text(encoding="utf-8"), r"^app_policy\(\) \{\n(.*?)^\}")
    compose_statements = _policy_statements(compose)
    script_statements = _policy_statements(script)

    assert len(compose_statements) == 2  # the parser found the bundled policy
    assert script_statements == compose_statements
    actions = {action for _, statement, _ in compose_statements for action in statement}
    resources = {resource for _, _, statement in compose_statements for resource in statement}
    assert {action for _, statement, _ in script_statements for action in statement} == actions
    assert {
        resource for _, _, statement in script_statements for resource in statement
    } == resources
    assert resources == {"arn:aws:s3:::<bucket>", "arn:aws:s3:::<bucket>/*"}


_MINIO_COMPLETE_ENV = {
    MINIO_OPT_IN_ENV: "1",
    MINIO_ENDPOINT_ENV: "http://127.0.0.1:4360",
    MINIO_BUCKET_ENV: "homayar-private",
    MINIO_ACCESS_KEY_ENV: "unit-app-key",
    MINIO_SECRET_KEY_ENV: "unit-app-secret",
    MINIO_DENIED_ACCESS_KEY_ENV: "unit-denied-key",
    MINIO_DENIED_SECRET_KEY_ENV: "unit-denied-secret",
    MINIO_PROBE_ACCESS_KEY_ENV: "unit-probe-key",
    MINIO_PROBE_SECRET_KEY_ENV: "unit-probe-secret",
}


@pytest.fixture
def minio_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name, value in _MINIO_COMPLETE_ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_minio_target_requires_the_opt_in(minio_env: pytest.MonkeyPatch) -> None:
    minio_env.delenv(MINIO_OPT_IN_ENV)
    reason = minio_unavailable_reason()
    assert reason is not None and MINIO_OPT_IN_ENV in reason
    minio_env.setenv(MINIO_OPT_IN_ENV, "true")
    assert minio_unavailable_reason() is not None  # only the exact value 1 opts in


@pytest.mark.parametrize("name", sorted(set(_MINIO_COMPLETE_ENV) - {MINIO_OPT_IN_ENV}))
def test_minio_target_requires_every_variable(minio_env: pytest.MonkeyPatch, name: str) -> None:
    minio_env.setenv(name, "  ")
    assert minio_unavailable_reason() == f"missing {name}"
    minio_env.delenv(name)
    assert minio_unavailable_reason() == f"missing {name}"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://minio.example.com:9000",
        "https://s3.amazonaws.com",
        "http://10.0.0.5:9000",
        "http://127.0.0.1.example.com:9000",
        "ftp://127.0.0.1:9000",
        "127.0.0.1:9000",
        "http://127.0.0.1:notaport",
        "http://127.0.0.1@evil.example/",  # userinfo: the real host is evil.example
        "http://localhost:4360@evil.example:4360",
        "http://localhost.example.com:4360",
    ],
)
def test_minio_target_refuses_a_non_loopback_endpoint(
    minio_env: pytest.MonkeyPatch, endpoint: str
) -> None:
    minio_env.setenv(MINIO_ENDPOINT_ENV, endpoint)
    reason = minio_unavailable_reason()
    assert reason is not None and MINIO_ENDPOINT_ENV in reason
    with pytest.raises(RuntimeError):
        minio_target()


@pytest.mark.parametrize(
    "endpoint",
    ["http://127.0.0.1:4360", "http://localhost:9000", "http://[::1]:9000", "https://127.0.0.1"],
)
def test_minio_target_accepts_a_loopback_endpoint(
    minio_env: pytest.MonkeyPatch, endpoint: str
) -> None:
    minio_env.setenv(MINIO_ENDPOINT_ENV, endpoint)
    assert minio_unavailable_reason() is None
    target = minio_target()
    assert target.endpoint_url == endpoint
    assert target.missing_bucket == "homayar-private-missing"
    shown = repr(target)
    for name in (
        MINIO_ACCESS_KEY_ENV,
        MINIO_SECRET_KEY_ENV,
        MINIO_DENIED_ACCESS_KEY_ENV,
        MINIO_DENIED_SECRET_KEY_ENV,
        MINIO_PROBE_ACCESS_KEY_ENV,
        MINIO_PROBE_SECRET_KEY_ENV,
    ):
        assert _MINIO_COMPLETE_ENV[name] not in shown
