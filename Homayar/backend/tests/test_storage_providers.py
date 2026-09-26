"""Focused tests for private storage and external-provider boundaries."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import httpx
import pytest
from pydantic import ValidationError

from backend.providers import (
    ASRRequest,
    DeliveryStatus,
    DiarizationRequest,
    DiarizationResult,
    DiarizationSegment,
    DiarizationStatus,
    EmailRequest,
    IntelligenceRequest,
    MockASRProvider,
    MockDiarizationProvider,
    MockEmailProvider,
    MockIntelligenceProvider,
    MockProviderDisabledError,
    MockSMSProvider,
    SMSRequest,
    UNKNOWN_SPEAKER_LABEL,
    UnavailableDiarizationProvider,
)
from backend.providers.asanak import AsanakSMSProvider
from backend.providers.whisper import LocalWhisperProvider
from backend.storage import (
    ChecksumMismatchError,
    InvalidObjectKeyError,
    InvalidPresignTTL,
    InvalidTenantError,
    LocalObjectStorage,
    ObjectListing,
    ObjectTooLargeError,
    PresignedAccessNotSupportedError,
    S3ObjectStorage,
    StorageError,
)
from backend.storage.base import tenant_prefix


FIXED_TOKEN = "a" * 32


class FakeS3NotFound(Exception):
    response = {
        "Error": {"Code": "NoSuchKey"},
        "ResponseMetadata": {"HTTPStatusCode": 404},
    }


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: Dict[str, Dict[str, Any]] = {}
        self.put_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self.presign_calls: list[Any] = []
        self.list_calls: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> Dict[str, Any]:
        body = kwargs["Body"].read()
        stored = {**kwargs, "Body": body}
        self.objects[kwargs["Key"]] = stored
        self.put_calls.append(stored)
        return {"ETag": "fake-etag"}

    def head_object(self, **kwargs: Any) -> Dict[str, Any]:
        try:
            stored = self.objects[kwargs["Key"]]
        except KeyError as exc:
            raise FakeS3NotFound() from exc
        return {"ContentLength": len(stored["Body"]), "Metadata": stored["Metadata"]}

    def get_object(self, **kwargs: Any) -> Dict[str, Any]:
        try:
            stored = self.objects[kwargs["Key"]]
        except KeyError as exc:
            raise FakeS3NotFound() from exc
        return {"Body": BytesIO(stored["Body"])}

    def delete_object(self, **kwargs: Any) -> Dict[str, Any]:
        self.delete_calls.append(kwargs)
        self.objects.pop(kwargs["Key"], None)
        return {}

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(kwargs)
        prefix = kwargs.get("Prefix", "")
        continuation = kwargs.get("ContinuationToken")
        matching = [
            {
                "Key": key,
                "Size": len(stored["Body"]),
                "LastModified": datetime.now(UTC),
            }
            for key, stored in sorted(self.objects.items())
            if key.startswith(prefix)
        ]
        page_size = 2
        start = int(continuation) if continuation is not None else 0
        page = matching[start : start + page_size]
        truncated = start + page_size < len(matching)
        response: dict[str, Any] = {
            "Contents": page,
            "IsTruncated": truncated,
        }
        if truncated:
            response["NextContinuationToken"] = str(start + page_size)
        return response

    def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        self.presign_calls.append((operation, kwargs))
        return f"https://objects.test/{kwargs['Params']['Key']}?signed=true"


def test_local_storage_streams_to_generated_tenant_key(tmp_path: Path) -> None:
    storage = LocalObjectStorage(
        tmp_path / "objects",
        max_size_bytes=1024,
        key_factory=lambda: FIXED_TOKEN,
    )
    payload = b"private-call-audio"

    result = storage.put_stream(
        "tenant_1",
        iter([payload[:7], payload[7:]]),
        content_type="audio/wav",
        original_filename="../../customer-call.wav",
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )

    assert result.key == f"tenants/tenant_1/objects/{FIXED_TOKEN}"
    assert "customer-call" not in result.key
    assert result.original_filename == "customer-call.wav"
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    with storage.open_reader("tenant_1", result.key) as reader:
        assert reader.read() == payload
    assert storage.get_metadata("tenant_1", result.key) == result

    object_path = storage.root.joinpath(*result.key.split("/"))
    assert stat.S_IMODE(object_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(storage.root.stat().st_mode) == 0o700


def test_local_storage_rejects_oversize_and_cleans_partial_file(tmp_path: Path) -> None:
    storage = LocalObjectStorage(
        tmp_path / "objects",
        max_size_bytes=5,
        key_factory=lambda: FIXED_TOKEN,
    )

    with pytest.raises(ObjectTooLargeError) as error:
        storage.put_stream("tenant", [b"123", b"456"], content_type="audio/wav")

    assert error.value.limit_bytes == 5
    assert not [path for path in storage.root.rglob("*") if path.is_file()]


def test_local_storage_rejects_bad_checksum_without_publication(tmp_path: Path) -> None:
    storage = LocalObjectStorage(
        tmp_path / "objects",
        max_size_bytes=100,
        key_factory=lambda: FIXED_TOKEN,
    )

    with pytest.raises(ChecksumMismatchError):
        storage.put_stream(
            "tenant",
            BytesIO(b"audio"),
            content_type="audio/wav",
            expected_sha256="0" * 64,
        )

    assert not [path for path in storage.root.rglob("*") if path.is_file()]


def test_local_storage_delete_is_tenant_scoped_and_presign_is_unsupported(tmp_path: Path) -> None:
    storage = LocalObjectStorage(
        tmp_path / "objects",
        max_size_bytes=100,
        key_factory=lambda: FIXED_TOKEN,
    )
    stored = storage.put_stream("tenant_a", [b"x"], content_type="application/octet-stream")

    with pytest.raises(InvalidObjectKeyError):
        storage.delete("tenant_b", stored.key)
    with pytest.raises(PresignedAccessNotSupportedError):
        storage.presign_get("tenant_a", stored.key)
    assert storage.delete("tenant_a", stored.key) is True
    assert storage.delete("tenant_a", stored.key) is False


def test_local_storage_refuses_symlink_deletion(tmp_path: Path) -> None:
    storage = LocalObjectStorage(
        tmp_path / "objects",
        max_size_bytes=100,
        key_factory=lambda: FIXED_TOKEN,
    )
    stored = storage.put_stream("tenant", [b"x"], content_type="application/octet-stream")
    object_path = storage.root.joinpath(*stored.key.split("/"))
    object_path.unlink()
    object_path.symlink_to(tmp_path / "outside")

    with pytest.raises(InvalidObjectKeyError):
        storage.delete("tenant", stored.key)


def test_s3_storage_checksums_upload_and_presigns() -> None:
    client = FakeS3Client()
    storage = S3ObjectStorage(
        "private-bucket",
        max_size_bytes=1024,
        client=client,
        native_checksum=True,
        key_factory=lambda: FIXED_TOKEN,
    )
    payload = b"streamed-to-s3"

    stored = storage.put_stream(
        "tenant",
        [payload[:4], payload[4:]],
        content_type="audio/mpeg",
        original_filename="call.mp3",
    )

    upload = client.put_calls[0]
    assert upload["Key"] == f"tenants/tenant/objects/{FIXED_TOKEN}"
    assert upload["Body"] == payload
    assert upload["Metadata"]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert upload["ChecksumSHA256"]
    with storage.open_reader("tenant", stored.key) as reader:
        assert reader.read() == payload

    access = storage.presign_get("tenant", stored.key, expires_in_seconds=60)
    assert access.method == "GET"
    assert "signed=true" in access.url
    assert client.presign_calls[0][1]["ExpiresIn"] == 60
    with pytest.raises(InvalidPresignTTL):
        storage.presign_get("tenant", stored.key, expires_in_seconds=901)


def test_s3_storage_enforces_limits_before_network_and_safe_delete() -> None:
    client = FakeS3Client()
    storage = S3ObjectStorage(
        "private-bucket",
        max_size_bytes=3,
        client=client,
        key_factory=lambda: FIXED_TOKEN,
    )

    with pytest.raises(ObjectTooLargeError):
        storage.put_stream("tenant", [b"abcd"], content_type="audio/wav")
    assert client.put_calls == []

    second_storage = S3ObjectStorage(
        "private-bucket",
        max_size_bytes=100,
        client=client,
        key_factory=lambda: FIXED_TOKEN,
    )
    stored = second_storage.put_stream("tenant_a", [b"ok"], content_type="audio/wav")
    with pytest.raises(InvalidObjectKeyError):
        second_storage.delete("tenant_b", stored.key)
    assert second_storage.delete("tenant_a", stored.key) is True
    assert second_storage.delete("tenant_a", stored.key) is False


def test_s3_storage_cleans_up_an_ambiguous_failed_upload() -> None:
    class AmbiguousS3Client(FakeS3Client):
        def put_object(self, **kwargs: Any) -> Dict[str, Any]:
            super().put_object(**kwargs)
            raise RuntimeError("connection ended after upload")

    client = AmbiguousS3Client()
    storage = S3ObjectStorage(
        "private-bucket",
        max_size_bytes=100,
        client=client,
        key_factory=lambda: FIXED_TOKEN,
    )

    with pytest.raises(StorageError) as error:
        storage.put_stream("tenant", [b"audio"], content_type="audio/wav")

    assert error.value.code == "s3_upload_failed"
    assert client.objects == {}
    assert client.delete_calls == [
        {"Bucket": "private-bucket", "Key": f"tenants/tenant/objects/{FIXED_TOKEN}"}
    ]


@pytest.mark.parametrize(
    "provider_factory",
    [
        MockASRProvider,
        MockDiarizationProvider,
        MockIntelligenceProvider,
        MockSMSProvider,
        MockEmailProvider,
    ],
)
def test_mock_providers_require_explicit_permission(provider_factory: Any) -> None:
    with pytest.raises(MockProviderDisabledError):
        provider_factory()


def test_mock_sms_and_email_are_deterministic_and_secret_safe() -> None:
    sms = MockSMSProvider(allow_mock=True)
    sms_request = SMSRequest(to="09121234567", body="OTP 876543", message_type="otp")
    first_sms = asyncio.run(sms.send(sms_request))
    second_sms = asyncio.run(sms.send(sms_request))
    assert first_sms == second_sms
    assert first_sms.status == DeliveryStatus.ACCEPTED
    assert "876543" not in repr(sms_request)

    email = MockEmailProvider(allow_mock=True)
    email_request = EmailRequest(
        to=["customer@example.com"],
        subject="Account notice",
        text_body="private email body",
    )
    first_email = asyncio.run(email.send(email_request))
    second_email = asyncio.run(email.send(email_request))
    assert first_email == second_email
    assert "private email body" not in repr(email_request)


def test_analysis_mocks_are_deterministic_and_never_assign_real_speakers() -> None:
    asr = MockASRProvider(allow_mock=True, transcript="Fixed transcript.", language="en")
    asr_request = ASRRequest(audio_path="/not/read/by/mock.wav")
    first_asr = asyncio.run(asr.transcribe(asr_request))
    second_asr = asyncio.run(asr.transcribe(asr_request))
    assert first_asr == second_asr
    assert first_asr.is_mock is True
    assert {segment.speaker_label for segment in first_asr.segments} == {UNKNOWN_SPEAKER_LABEL}

    diarization = MockDiarizationProvider(allow_mock=True)
    diarization_request = DiarizationRequest(audio_path="/not/read/by/mock.wav")
    first_diarization = asyncio.run(diarization.diarize(diarization_request))
    second_diarization = asyncio.run(diarization.diarize(diarization_request))
    assert first_diarization == second_diarization
    assert first_diarization.status == DiarizationStatus.UNAVAILABLE

    intelligence = MockIntelligenceProvider(allow_mock=True)
    intelligence_request = IntelligenceRequest(transcript_text="  A fixed   transcript.  ")
    first_intelligence = asyncio.run(intelligence.analyze(intelligence_request))
    second_intelligence = asyncio.run(intelligence.analyze(intelligence_request))
    assert first_intelligence == second_intelligence
    assert first_intelligence.summary == "A fixed transcript."


def test_whisper_uses_injected_model_and_never_fabricates_speakers(tmp_path: Path) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"not-empty")

    class FakeWhisper:
        def __init__(self) -> None:
            self.kwargs: Dict[str, Any] = {}

        def transcribe(self, path: str, **kwargs: Any) -> Any:
            self.kwargs = kwargs
            return iter(
                [
                    SimpleNamespace(start=0.0, end=1.0, text=" Hello "),
                    SimpleNamespace(start=1.0, end=2.0, text="world"),
                ]
            ), SimpleNamespace(language="en", duration=2.0)

    model = FakeWhisper()
    provider = LocalWhisperProvider(
        model_name="injected-model",
        device="injected-device",
        compute_type="int8",
        model=model,
    )
    result = asyncio.run(provider.transcribe(ASRRequest(audio_path=str(audio_path), language="en")))

    assert result.text == "Hello world"
    assert model.kwargs == {"beam_size": 5, "vad_filter": True, "language": "en"}
    assert {segment.speaker_label for segment in result.segments} == {UNKNOWN_SPEAKER_LABEL}
    assert "agent" not in result.model_dump_json()
    assert "customer" not in result.model_dump_json()


def test_unavailable_diarization_is_explicit_and_disallows_fake_labels() -> None:
    request = DiarizationRequest(audio_path="/private/audio.wav")
    result = asyncio.run(UnavailableDiarizationProvider().diarize(request))
    assert result.status == DiarizationStatus.UNAVAILABLE
    assert result.issue is not None
    assert result.issue.code == "diarization_unavailable"
    assert result.segments == []

    with pytest.raises(ValidationError):
        DiarizationResult(
            provider="bad",
            status=DiarizationStatus.UNAVAILABLE,
            issue={"code": "diarization_unavailable", "message": "unavailable"},
            segments=[
                DiarizationSegment(
                    speaker_label="speaker_1",
                    start_seconds=0,
                    end_seconds=10,
                )
            ],
        )


def test_asanak_uses_injected_settings_without_logging_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    captured: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"meta": {"status": 200}, "data": [{"messageId": "asanak-123"}]},
        )

    async def run() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = AsanakSMSProvider(
                url="https://sms.test/send",
                token="super-secret-token",
                sender="30001234",
                client=client,
            )
            return await provider.send(
                SMSRequest(to="09121234567", body="OTP 654321", message_type="otp")
            )

    result = asyncio.run(run())

    assert result.status == DeliveryStatus.ACCEPTED
    assert result.message_id == "asanak-123"
    assert captured["headers"]["authorization"] == "Bearer super-secret-token"
    assert captured["payload"] == {
        "source": "30001234",
        "destination": "09121234567",
        "message": "OTP 654321",
    }
    assert "super-secret-token" not in caplog.text
    assert "654321" not in caplog.text


def test_asanak_sanitizes_error_responses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="failed to send OTP 999999 with token secret")

    async def run() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = AsanakSMSProvider(
                url="https://sms.test/send",
                token="secret",
                sender="30001234",
                client=client,
            )
            return await provider.send(SMSRequest(to="09121234567", body="OTP 999999"))

    result = asyncio.run(run())
    serialized = result.model_dump_json()
    assert result.status == DeliveryStatus.FAILED
    assert result.issue is not None and result.issue.retryable is True
    assert "999999" not in serialized
    assert "secret" not in serialized


# --- caller-chosen object identity (WP06-T03 attempt-scoped artifacts) -----------


def test_local_storage_token_writes_are_idempotent_on_content_and_detect_conflicts(
    tmp_path: Path,
) -> None:
    from backend.storage import ObjectConflictError, ObjectIncompleteError

    storage = LocalObjectStorage(tmp_path / "objects", max_size_bytes=1024)
    token = "art-attempt-0001-asr-token"
    first = storage.put_stream(
        "tenant-a", BytesIO(b'{"a":1}'), content_type="application/json", object_token=token
    )
    assert first.key == f"tenants/tenant-a/objects/{token}"
    again = storage.put_stream(
        "tenant-a", BytesIO(b'{"a":1}'), content_type="application/json", object_token=token
    )
    assert again.key == first.key and again.sha256 == first.sha256
    with storage.open_reader("tenant-a", first.key) as reader:
        assert reader.read() == b'{"a":1}'  # never rewritten
    with pytest.raises(ObjectConflictError) as conflict:
        storage.put_stream(
            "tenant-a", BytesIO(b'{"a":2}'), content_type="application/json", object_token=token
        )
    assert conflict.value.code == "object_conflict"
    # the same token under another tenant is another object
    other = storage.put_stream(
        "tenant-b", BytesIO(b'{"a":2}'), content_type="application/json", object_token=token
    )
    assert other.key != first.key
    with pytest.raises(InvalidObjectKeyError):
        storage.put_stream(
            "tenant-a", BytesIO(b"x"), content_type="application/json", object_token="../escape"
        )
    with pytest.raises(InvalidObjectKeyError):
        storage.put_stream(
            "tenant-a", BytesIO(b"x"), content_type="text/plain", object_token="short"
        )
    # an object whose metadata never landed is reported, not silently reused
    (tmp_path / "objects" / "tenants" / "tenant-a" / "objects" / f"{token}.metadata.json").unlink()
    with pytest.raises(ObjectIncompleteError):
        storage.put_stream(
            "tenant-a", BytesIO(b'{"a":1}'), content_type="application/json", object_token=token
        )


class FakeConditionalS3Client(FakeS3Client):
    """Honours ``IfNoneMatch="*"`` like S3: an existing key fails with 412."""

    class PreconditionFailed(Exception):
        response = {
            "Error": {"Code": "PreconditionFailed"},
            "ResponseMetadata": {"HTTPStatusCode": 412},
        }

    def put_object(self, **kwargs: Any) -> Dict[str, Any]:
        if kwargs.get("IfNoneMatch") == "*" and kwargs["Key"] in self.objects:
            raise self.PreconditionFailed()
        return super().put_object(**kwargs)


def test_s3_storage_token_writes_use_conditional_put_and_compare_checksums() -> None:
    from backend.storage import ObjectConflictError

    client = FakeConditionalS3Client()
    storage = S3ObjectStorage("private-bucket", max_size_bytes=1024, client=client)
    token = "art-attempt-0001-asr-token"
    first = storage.put_stream(
        "tenant-a", BytesIO(b"payload"), content_type="application/json", object_token=token
    )
    assert client.put_calls[-1]["IfNoneMatch"] == "*"
    again = storage.put_stream(
        "tenant-a", BytesIO(b"payload"), content_type="application/json", object_token=token
    )
    assert again.key == first.key and again.sha256 == first.sha256
    assert len(client.put_calls) == 1  # the second write never replaced the object
    with pytest.raises(ObjectConflictError):
        storage.put_stream(
            "tenant-a", BytesIO(b"other"), content_type="application/json", object_token=token
        )
    assert client.delete_calls == []  # a caller-chosen key is never cleaned up on failure


def test_local_storage_lists_owned_objects_with_completeness_and_purges_leftovers(
    tmp_path: Path,
) -> None:
    storage = LocalObjectStorage(tmp_path / "objects", max_size_bytes=1024)

    # Two objects for tenant A
    a1 = storage.put_stream(
        "tenantA",
        BytesIO(b"content-a1"),
        content_type="audio/wav",
        object_token="a" * 32,
    )
    a2 = storage.put_stream(
        "tenantA",
        BytesIO(b"content-a2"),
        content_type="audio/wav",
        object_token="b" * 32,
    )

    # One object for tenant B
    b1 = storage.put_stream(
        "tenantB",
        BytesIO(b"content-b1"),
        content_type="audio/wav",
        object_token="c" * 32,
    )

    # Write a .upload-abc temporary in A's directory with old mtime
    a_dir = tmp_path / "objects" / "tenants" / "tenantA" / "objects"
    temp_file = a_dir / ".upload-abc"
    temp_file.write_bytes(b"temporary data")
    old_time = datetime.now(UTC).timestamp() - 7200
    os.utime(temp_file, (old_time, old_time))

    # Object file without sidecar (32 hex chars) with old mtime
    orphan_name = "d" * 32
    orphan_file = a_dir / orphan_name
    orphan_file.write_bytes(b"incomplete object")
    os.utime(orphan_file, (old_time, old_time))

    # iter_objects yields A's two complete + sidecar-less incomplete
    items = sorted(storage.iter_objects("tenantA"), key=lambda x: x.key)
    assert len(items) == 3
    keys = {item.key for item in items}
    assert a1.key in keys
    assert a2.key in keys
    orphan_key = f"tenants/tenantA/objects/{orphan_name}"
    assert orphan_key in keys

    # Never B's key
    assert b1.key not in keys

    # Completeness
    for item in items:
        assert isinstance(item, ObjectListing)
        assert item.last_modified is not None
        assert item.last_modified.tzinfo is not None
        if item.key == orphan_key:
            assert item.complete is False
            assert item.size_bytes == len(b"incomplete object")
        else:
            assert item.complete is True

    # purge_incomplete removes the temporary (returns 1)
    now = datetime.now(UTC)
    purged = storage.purge_incomplete("tenantA", older_than=now)
    assert purged == 1
    assert not temp_file.exists()

    # Objects still exist
    assert storage.exists("tenantA", a1.key)
    assert storage.exists("tenantA", a2.key)
    assert orphan_file.exists()

    # Unknown tenant yields nothing
    assert list(storage.iter_objects("unknown_tenant")) == []


def test_local_storage_wraps_listing_and_purge_failures_and_validates_the_tenant(
    tmp_path: Path, monkeypatch
) -> None:
    storage = LocalObjectStorage(tmp_path / "objects", max_size_bytes=1024)
    storage.put_stream(
        "tenantA",
        BytesIO(b"content-a1"),
        content_type="audio/wav",
        object_token="a" * 32,
    )
    now = datetime.now(UTC)

    # Invalid tenant ids are refused before any filesystem access
    with pytest.raises(InvalidTenantError):
        list(storage.iter_objects("../tenantA"))
    with pytest.raises(InvalidTenantError):
        storage.purge_incomplete("../tenantA", older_than=now)

    # Filesystem failures surface as StorageError with a stable code
    def _denied(self):
        raise PermissionError("simulated")

    monkeypatch.setattr(Path, "iterdir", _denied)
    with pytest.raises(StorageError) as listing_failure:
        list(storage.iter_objects("tenantA"))
    assert listing_failure.value.code == "local_list_failed"
    with pytest.raises(StorageError) as purge_failure:
        storage.purge_incomplete("tenantA", older_than=now)
    assert purge_failure.value.code == "local_purge_failed"


def test_s3_storage_lists_the_tenant_prefix_with_pagination() -> None:
    client = FakeS3Client()
    storage = S3ObjectStorage("private-bucket", max_size_bytes=1024, client=client)

    # Three objects for tenant A
    for token in ("a" * 32, "b" * 32, "c" * 32):
        storage.put_stream(
            "tenantA",
            BytesIO(f"content-{token[0]}".encode()),
            content_type="application/octet-stream",
            object_token=token,
        )

    # One object for tenant B
    storage.put_stream(
        "tenantB",
        BytesIO(b"content-b"),
        content_type="application/octet-stream",
        object_token="d" * 32,
    )

    # Plant a foreign key with an invalid token under A's prefix
    foreign_key = f"{tenant_prefix('tenantA')}short"
    client.objects[foreign_key] = {
        "Body": b"foreign",
        "Metadata": {},
        "Key": foreign_key,
        "ContentType": "application/octet-stream",
        "Bucket": "private-bucket",
    }

    items = list(storage.iter_objects("tenantA"))
    assert len(items) == 3
    assert all(item.complete is True for item in items)
    assert all(item.size_bytes > 0 for item in items)
    assert all(item.last_modified is not None for item in items)

    # Pagination: 4 keys under prefix, page_size=2, two calls
    assert len(client.list_calls) == 2
    assert client.list_calls[0]["Prefix"] == tenant_prefix("tenantA")

    # A client that raises → StorageError with code s3_list_failed
    class _FailingListClient:
        def list_objects_v2(self, **kwargs):
            raise RuntimeError("network error")

    failing = S3ObjectStorage(
        "private-bucket",
        max_size_bytes=1024,
        client=_FailingListClient(),
    )
    with pytest.raises(StorageError) as exc_info:
        list(failing.iter_objects("tenantA"))
    assert exc_info.value.code == "s3_list_failed"
