"""Shared object-storage contract suite (WP05-T05).

One set of guarantees, run against every supported ``ObjectStorage``:

* ``local`` — the real ``LocalObjectStorage`` on a disposable directory;
* ``s3`` — ``S3ObjectStorage`` over a controlled fake client that models
  conditional creates, 403/404/412, transport failures, pagination and
  response bodies (adapter-contract evidence only: a fake cannot establish
  cloud IAM policy or the behaviour of every S3-compatible service);
* ``live`` — ``S3ObjectStorage`` over a real boto3 client against a
  *designated disposable* bucket, opt-in only (``backend/tests/_live_s3.py``);
  skipped by default and reported as NOT_RUN / BLOCKED_EXTERNAL.

Fault injection (denied / transient / missing bucket) exists for ``local``
and ``s3`` only; tests that need it skip the live target instead of
pretending.  The single-request upload proof at the end runs the *actual*
boto3 SDK path through ``botocore.stub.Stubber`` and records every
operation name the adapter issues.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import inspect
import os
import pathlib
import tempfile
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
)
from backend.storage import s3 as s3_module
from backend.storage.base import tenant_prefix
from backend.tests._live_s3 import (
    LIVE_S3_MAX_OBJECT_BYTES,
    live_s3_target,
    live_s3_unavailable_reason,
    quiet_sdk_logging,
)

MAX_OBJECT_BYTES = 4096
TOKEN_A = "contract-token-aaaaaaaa0001"  # noqa: S105 - object token, not a credential
TOKEN_B = "contract-token-bbbbbbbb0002"  # noqa: S105 - object token, not a credential
TOKEN_C = "contract-token-cccccccc0003"  # noqa: S105 - object token, not a credential
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

_OP_NAMES = {
    "put": "PutObject",
    "head": "HeadObject",
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
    * ``list_objects_v2`` paginates with ``page_size`` and can emit an empty
      truncated first page (permitted by the S3 API);
    * per-operation faults: ``deny`` (403 ``AccessDenied``), ``transient``
      (``EndpointConnectionError``), ``bucket_missing`` (``NoSuchBucket`` 404),
      ``incomplete`` (``get_object`` without a body).

    Every API operation name is appended to ``operations``.
    """

    def __init__(self, *, page_size: int = 2, empty_first_page: bool = False) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.operations: list[str] = []
        self.faults: dict[str, str] = {}
        self.bodies: list[_TrackingBody] = []
        self.page_size = page_size
        self.empty_first_page = empty_first_page

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
        stored = self.objects.get(kwargs["Key"])
        if stored is None:
            raise _client_error("404", 404, "HeadObject")
        return {"ContentLength": len(stored["Body"]), "Metadata": stored["Metadata"]}

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


@pytest.fixture(
    params=[
        pytest.param("local", id="local"),
        pytest.param("s3", id="s3-fake"),
        pytest.param("live", id="s3-live", marks=pytest.mark.live_s3),
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
    reason = live_s3_unavailable_reason()
    if reason is not None:  # defence in depth: the marker skip already covers this
        pytest.skip(f"NOT_RUN/BLOCKED_EXTERNAL: {reason}")
    previous_levels = quiet_sdk_logging()
    harness = _live_harness()
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
    assert storage.exists("tenant-a", key) is False

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
        "HeadObject",
        "GetObject",
        "HeadObject",
        "ListObjectsV2",
    ]
