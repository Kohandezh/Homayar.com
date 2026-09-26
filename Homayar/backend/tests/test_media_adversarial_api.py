"""WP05-T06: adversarial media through the real upload route with real binaries.

The T02 fixture tests prove the validator; the ledger tests prove the refusal
bookkeeping with a mocked validator.  This module joins the two: forged,
corrupt, truncated and oversized fixtures travel through ``POST /uploads``
with ffprobe/ffmpeg actually running, and a *decoding timeout* is retried.
Every refusal must be contained — no meeting, no object, no quarantine file,
original bytes untouched — and the retry after a transient failure must
create exactly one chain.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from backend.config import Settings
from backend.routers.v1 import meetings as meetings_router
from backend.storage import create_object_storage
from backend.tests.test_ingestion_ledger import (
    _audits,
    _counts,
    _headers,
    _patch_media,
    _tenant,
)
from backend.tests.test_media_fixtures import build_media_fixtures

pytestmark = pytest.mark.ffmpeg

_QUARANTINE = "quarantine"


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_media_fixtures(tmp_path_factory.mktemp("media-api"))


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """The conftest settings with a 1 MiB cap and the bounded decode enabled."""
    return settings.model_copy(update={"max_upload_size_mb": 1, "media_decode_probe_seconds": 5.0})


def _post(client, tenant: dict, *, key: str, path: Path, mime: str, filename: str | None = None):
    return client.post(
        "/api/v1/uploads",
        headers=_headers(tenant, **{"Idempotency-Key": key}),
        cookies=tenant["cookies"],
        data={"title": "Adversarial", "language": "fa"},
        files={"file": (filename or path.name, path.read_bytes(), mime)},
    )


def _quarantine_files(settings: Settings) -> list[str]:
    root = Path(settings.upload_quarantine_dir)
    return (
        sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
        if root.exists()
        else []
    )


def _stored_keys(settings: Settings, workspace_id: str) -> list[str]:
    return [entry.key for entry in create_object_storage(settings).iter_objects(workspace_id)]


@pytest.mark.parametrize(
    ("name", "mime", "status", "code"),
    [
        ("forged.mp3", "audio/mpeg", 422, "extension_content_mismatch"),
        ("corrupt.mp3", "audio/mpeg", 422, "media_decode_failed"),
        ("truncated.m4a", "audio/mp4", 422, "malformed_media"),
        ("huge-id3.mp3", "audio/mpeg", 422, "malformed_media"),
        ("wide.wav", "audio/wav", 422, "invalid_audio_stream"),
        ("claims-hour.wav", "audio/wav", 422, "media_decode_failed"),
    ],
)
def test_terminal_refusals_are_contained_and_replay_deterministically(
    client, email_outbox, monkeypatch, settings, fixtures: Path, name, mime, status, code
) -> None:
    # _patch_media mocks the validator; restore the real one — only dispatch stays captured.
    queued = _patch_media(monkeypatch)
    from backend.media import validate_media_upload as real_validate

    monkeypatch.setattr(meetings_router, "validate_media_upload", real_validate)
    tenant = _tenant(client, email_outbox, f"adv-{name.replace('.', '-')}")
    before = (fixtures / name).read_bytes()

    refused = _post(client, tenant, key=f"evt-terminal-{name}", path=fixtures / name, mime=mime)
    assert refused.status_code == status, refused.text
    error = refused.json()["error"]
    assert error["code"] == code
    ingestion_id = error["details"]["ingestion_id"]
    assert error["details"] == {"ingestion_id": ingestion_id, "reason": code}
    assert "Retry-After" not in refused.headers  # terminal: retrying the same bytes is pointless

    # Containment: nothing created, nothing stored, nothing left in quarantine.
    assert _counts() == {"meetings": 0, "recordings": 0, "jobs": 0, "ingestions": 1}
    assert queued == []
    assert _stored_keys(settings, tenant["workspace_id"]) == []
    assert _quarantine_files(settings) == []
    assert (fixtures / name).read_bytes() == before

    # The same bytes under the same key replay the recorded refusal without
    # inspecting again (a broken validator would now answer differently).
    def never(*_args, **_kwargs):
        raise AssertionError("the bytes must not be inspected again")

    monkeypatch.setattr(meetings_router, "validate_media_upload", never)
    replay = _post(client, tenant, key=f"evt-terminal-{name}", path=fixtures / name, mime=mime)
    assert replay.status_code == status, replay.text
    assert replay.json()["error"]["details"] == {
        "ingestion_id": ingestion_id,
        "reason": code,
        "replayed": True,
    }
    assert _counts()["ingestions"] == 1


def test_oversized_upload_is_refused_while_staging_and_leaves_nothing(
    client, email_outbox, monkeypatch, settings, fixtures: Path, tmp_path: Path
) -> None:
    queued = _patch_media(monkeypatch)
    from backend.media import validate_media_upload as real_validate

    monkeypatch.setattr(meetings_router, "validate_media_upload", real_validate)
    tenant = _tenant(client, email_outbox, "adv-oversize")
    big = tmp_path / "big.wav"
    big.write_bytes((fixtures / "ok.wav").read_bytes() + bytes(1024 * 1024))  # > 1 MiB cap

    inspections: list[str] = []
    monkeypatch.setattr(
        meetings_router,
        "validate_media_upload",
        lambda *a, **k: inspections.append("probe") or real_validate(*a, **k),
    )
    refused = _post(client, tenant, key="evt-oversize-0001", path=big, mime="audio/wav")
    assert refused.status_code == 413, refused.text
    assert refused.json()["error"]["code"] == "upload_too_large"
    assert inspections == []  # refused by the streaming cap before any tool ran
    assert _counts() == {"meetings": 0, "recordings": 0, "jobs": 0, "ingestions": 0}
    assert queued == []
    assert _stored_keys(settings, tenant["workspace_id"]) == []
    assert _quarantine_files(settings) == []
    audit = _audits("ingestion.rejected")[-1]
    assert audit.details["reason"] == "upload_too_large"


def test_decoding_timeout_is_transient_and_the_retry_creates_one_chain(
    client, email_outbox, monkeypatch, settings, fixtures: Path
) -> None:
    queued = _patch_media(monkeypatch)
    from backend.media import validate_media_upload as real_validate

    tenant = _tenant(client, email_outbox, "adv-timeout")
    payload = (fixtures / "ok.mp3").read_bytes()

    def decode_too_slowly(*args, **kwargs):
        kwargs["ffmpeg_timeout_seconds"] = 0.01  # the real ffmpeg cannot finish in time
        return real_validate(*args, **kwargs)

    monkeypatch.setattr(meetings_router, "validate_media_upload", decode_too_slowly)
    timed_out = _post(
        client, tenant, key="evt-slow-decode-0001", path=fixtures / "ok.mp3", mime="audio/mpeg"
    )
    assert timed_out.status_code == 503, timed_out.text
    assert timed_out.json()["error"]["code"] == "media_decode_timeout"
    assert "details" not in timed_out.json()["error"]  # no ledger row: the identity stays free
    assert timed_out.headers.get("Retry-After") == "30"
    assert _counts() == {"meetings": 0, "recordings": 0, "jobs": 0, "ingestions": 0}
    assert _stored_keys(settings, tenant["workspace_id"]) == []
    assert _quarantine_files(settings) == []
    transient = _audits("ingestion.rejected")[-1]
    assert transient.outcome == "transient" and transient.details["terminal"] is False
    assert transient.details["reason"] == "media_decode_timeout"

    # Retry with the same key once the decoder has time: accepted, once.
    monkeypatch.setattr(meetings_router, "validate_media_upload", real_validate)
    accepted = _post(
        client, tenant, key="evt-slow-decode-0001", path=fixtures / "ok.mp3", mime="audio/mpeg"
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["idempotent_replay"] is False
    assert accepted.json()["ingestion"]["status"] == "accepted"
    assert _counts() == {"meetings": 1, "recordings": 1, "jobs": 1, "ingestions": 1}
    assert len(queued) == 1

    # The stored original is the submitted bytes, verified by digest.
    keys = _stored_keys(settings, tenant["workspace_id"])
    assert len(keys) == 1
    with create_object_storage(settings).open_reader(tenant["workspace_id"], keys[0]) as reader:
        assert hashlib.sha256(reader.read()).hexdigest() == hashlib.sha256(payload).hexdigest()
    assert _quarantine_files(settings) == []

    # Replaying the accepted event is idempotent: still one chain.
    replay = _post(
        client, tenant, key="evt-slow-decode-0001", path=fixtures / "ok.mp3", mime="audio/mpeg"
    )
    assert replay.status_code == 202 and replay.json()["idempotent_replay"] is True
    assert _counts() == {"meetings": 1, "recordings": 1, "jobs": 1, "ingestions": 1}
    assert len(queued) == 1


def test_duration_bound_refuses_real_audio_with_a_terminal_413(
    client, email_outbox, monkeypatch, settings, fixtures: Path
) -> None:
    from backend.media import MediaValidationPolicy
    from backend.media import validate_media_upload as real_validate

    queued = _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "adv-duration")

    def tight_policy(*args, **kwargs):
        kwargs["policy"] = MediaValidationPolicy(max_duration_seconds=2, decode_probe_seconds=5)
        return real_validate(*args, **kwargs)  # ffprobe reads the real three seconds

    monkeypatch.setattr(meetings_router, "validate_media_upload", tight_policy)
    refused = _post(
        client, tenant, key="evt-duration-0001", path=fixtures / "ok.wav", mime="audio/wav"
    )
    assert refused.status_code == 413, refused.text
    error = refused.json()["error"]
    assert error["code"] == "media_duration_exceeded"
    assert error["details"]["reason"] == "media_duration_exceeded"
    assert "Retry-After" not in refused.headers
    assert _counts() == {"meetings": 0, "recordings": 0, "jobs": 0, "ingestions": 1}
    assert queued == []
    assert _stored_keys(settings, tenant["workspace_id"]) == []
    assert _quarantine_files(settings) == []
