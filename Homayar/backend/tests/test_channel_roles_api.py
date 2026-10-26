"""HOM-37: the trusted channel-role mapping routes and ingest-time attribution.

Admin writes (``workspace:manage`` + CSRF, audited) set a mapping per ingestion
source; uploads and voicemail imports attribute channels with the mapping of
their own source, once, at ingest.  The per-recording record is kept by
``backend.media.channel_store`` in ``media_objects.channel_inspection``; these
tests read it back through meeting detail and from the column.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.database.saas_models import (
    MediaObject,
    Membership,
    Recording,
    Workspace,
    WorkspaceRole,
)
from backend.media import channel_store
from backend.media import validate_media_upload as real_validate
from backend.media.channel_roles import SETTINGS_KEY
from backend.routers.v1 import meetings as meetings_router
from backend.security.context import CSRF_COOKIE
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_channel_inspection import build_channel_fixtures
from backend.tests.test_ingestion_ledger import (
    _audits,
    _cookies,
    _headers,
    _patch_media,
    _tenant,
    _upload,
)

BASE = "/api/v1/admin/channel-role-mappings"
CONSOLE = f"{BASE}/file_upload/console"
AGENT_CUSTOMER = {"channels": [{"channel": 0, "role": "agent"}, {"channel": 1, "role": "customer"}]}
CUSTOMER_AGENT = {"channels": [{"channel": 0, "role": "customer"}, {"channel": 1, "role": "agent"}]}


@pytest.fixture
def store():
    """The production store: the ``media_objects.channel_inspection`` column."""
    return None


def _kept_records(workspace_id: str) -> list[MediaObject]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(MediaObject).where(
                    MediaObject.workspace_id == workspace_id,
                    MediaObject.channel_inspection.is_not(None),
                )
            )
        )


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_channel_fixtures(tmp_path_factory.mktemp("channel-api"))


def _error(response) -> str:
    return response.json()["error"]["code"]


def _put(client, tenant: dict, body, *, path: str = CONSOLE, headers: dict | None = None):
    return client.put(
        path,
        json=body,
        headers=headers if headers is not None else _headers(tenant),
        cookies=tenant["cookies"],
    )


def _get(client, tenant: dict, path: str = BASE):
    return client.get(path, headers=_headers(tenant), cookies=tenant["cookies"])


def _delete(client, tenant: dict, path: str = CONSOLE, *, headers: dict | None = None):
    return client.delete(
        path,
        headers=headers if headers is not None else _headers(tenant),
        cookies=tenant["cookies"],
    )


def _settings_json(workspace_id: str) -> dict:
    with inspection_session_factory()() as db:
        return copy.deepcopy(db.get(Workspace, workspace_id).settings_json)


def _member(client, email_outbox, owner: dict, *, label: str, role: WorkspaceRole) -> dict:
    """A second user with ``role`` in the owner's workspace (its own session)."""
    credentials = register_user(
        client, email_outbox, email=f"channels-{label}@example.com", name=f"Channels {label}"
    )
    session = login_user(client, credentials)
    with inspection_session_factory()() as db:
        db.add(
            Membership(workspace_id=owner["workspace_id"], user_id=session["user"]["id"], role=role)
        )
        db.commit()
    return {
        "user_id": session["user"]["id"],
        "workspace_id": owner["workspace_id"],
        "cookies": _cookies(client),
    }


def _real_upload(client, tenant: dict, path: Path, *, key: str):
    mime = {".flac": "audio/flac", ".wav": "audio/wav"}[path.suffix]
    return client.post(
        "/api/v1/uploads",
        headers=_headers(tenant, **{"Idempotency-Key": key}),
        cookies=tenant["cookies"],
        data={"title": "Channel roles", "language": "fa"},
        files={"file": (path.name, path.read_bytes(), mime)},
    )


def _with_real_media(monkeypatch) -> list[str]:
    queued = _patch_media(monkeypatch)  # captures dispatch; the validator is restored below
    monkeypatch.setattr(meetings_router, "validate_media_upload", real_validate)
    return queued


def _inspection(client, tenant: dict, meeting_id: str):
    detail = client.get(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )
    assert detail.status_code == 200, detail.text
    return detail.json()["channel_inspection"]


def _roles(inspection: dict) -> list[tuple[str, str, str | None]]:
    return [
        (channel["role"], channel["attribution"], channel["reason"])
        for channel in inspection["channels"]
    ]


# --- the admin routes (criterion 7) ---------------------------------------------------


def test_owner_sets_lists_changes_and_removes_a_mapping_with_audit(client, email_outbox):
    owner = _tenant(client, email_outbox, "ch-owner")

    created = _put(client, owner, AGENT_CUSTOMER)
    assert created.status_code == 200, created.text
    body = created.json()
    assert set(body) == {
        "source_type",
        "source_provider",
        "version",
        "digest",
        "channels",
        "updated_at",
        "updated_by_user_id",
    }
    assert (body["source_type"], body["source_provider"], body["version"]) == (
        "file_upload",
        "console",
        1,
    )
    assert len(body["digest"]) == 64
    assert body["channels"] == AGENT_CUSTOMER["channels"]
    assert body["updated_by_user_id"] == owner["user_id"]

    listed = _get(client, owner)
    assert listed.status_code == 200, listed.text
    assert listed.json() == {"items": [body]}
    stored = _settings_json(owner["workspace_id"])[SETTINGS_KEY]["file_upload:console"]
    assert stored["version"] == 1 and stored["active"] is True

    audits = [
        a for a in _audits("workspace.settings.updated") if a.workspace_id == owner["workspace_id"]
    ]
    assert len(audits) == 1
    event = audits[0]
    assert event.actor_user_id == owner["user_id"]
    assert (event.target_type, event.target_id) == ("workspace", owner["workspace_id"])
    assert event.details["fields"] == "channel_role_mappings"
    assert event.details["change_kind"] == "set"
    assert event.details["source_type"] == "file_upload"
    assert event.details["mapping_version"] == 1
    assert event.details["mapping_digest"] == body["digest"]
    assert event.details["previous_version"] is None
    assert event.details["channels"] == [
        {"channel": 0, "mapped_role": "agent"},
        {"channel": 1, "mapped_role": "customer"},
    ]

    # The same content again changes nothing and writes no audit row.
    again = _put(client, owner, {"channels": list(reversed(AGENT_CUSTOMER["channels"]))})
    assert again.status_code == 200 and again.json()["version"] == 1
    assert again.json()["digest"] == body["digest"]
    assert len(_audits("workspace.settings.updated")) == 1

    swapped = _put(client, owner, CUSTOMER_AGENT)
    assert swapped.status_code == 200 and swapped.json()["version"] == 2
    assert swapped.json()["digest"] != body["digest"]
    second = _audits("workspace.settings.updated")[-1]
    assert second.details["previous_version"] == 1
    assert second.details["previous_digest"] == body["digest"]

    removed = _delete(client, owner)
    assert removed.status_code == 204, removed.text
    assert removed.content == b""
    third = _audits("workspace.settings.updated")[-1]
    assert third.details["change_kind"] == "removed"
    assert third.details["mapping_version"] == 3
    assert third.details["previous_version"] == 2
    assert _get(client, owner).json() == {"items": []}

    missing = _delete(client, owner)
    assert missing.status_code == 404
    assert _error(missing) == "channel_role_mapping_not_found"
    assert len(_audits("workspace.settings.updated")) == 3

    # A version number is never reused after a removal.
    back = _put(client, owner, {"channels": [{"channel": 0, "role": "agent"}]})
    assert back.status_code == 200 and back.json()["version"] == 4


def test_other_settings_survive_and_the_settings_route_is_unchanged(client, email_outbox):
    owner = _tenant(client, email_outbox, "ch-settings")
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, owner["workspace_id"])
        workspace.settings_json = {"unrelated": {"kept": True}}
        db.commit()
    assert _put(client, owner, AGENT_CUSTOMER).status_code == 200
    assert _put(client, owner, AGENT_CUSTOMER, path=f"{BASE}/file_upload/api").status_code == 200
    settings_json = _settings_json(owner["workspace_id"])
    assert settings_json["unrelated"] == {"kept": True}
    assert set(settings_json[SETTINGS_KEY]) == {"file_upload:console", "file_upload:api"}
    assert [item["source_provider"] for item in _get(client, owner).json()["items"]] == [
        "api",
        "console",
    ]

    settings = client.get(
        "/api/v1/admin/workspace-settings", headers=_headers(owner), cookies=owner["cookies"]
    )
    assert settings.status_code == 200
    assert SETTINGS_KEY not in settings.json() and "settings_json" not in settings.json()
    refused = client.patch(
        "/api/v1/admin/workspace-settings",
        json={"settings_json": {SETTINGS_KEY: {}}},
        headers=_headers(owner),
        cookies=owner["cookies"],
    )
    assert refused.status_code == 422


def test_an_admin_may_write_and_members_viewers_and_api_keys_may_not(client, email_outbox):
    owner = _tenant(client, email_outbox, "ch-roles")
    admin = _member(client, email_outbox, owner, label="admin", role=WorkspaceRole.ADMIN)
    member = _member(client, email_outbox, owner, label="member", role=WorkspaceRole.MEMBER)
    viewer = _member(client, email_outbox, owner, label="viewer", role=WorkspaceRole.VIEWER)

    # Allow-control: the same payload succeeds for an admin.
    allowed = _put(client, admin, AGENT_CUSTOMER)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["updated_by_user_id"] == admin["user_id"]
    assert _audits("workspace.settings.updated")[-1].actor_user_id == admin["user_id"]
    before = _settings_json(owner["workspace_id"])

    for persona in (member, viewer):
        for response in (
            _get(client, persona),
            _put(client, persona, CUSTOMER_AGENT),
            _delete(client, persona),
        ):
            assert response.status_code == 403, response.text
            assert _error(response) == "permission_denied"
    assert _settings_json(owner["workspace_id"]) == before

    # An API key cannot carry workspace:manage: refused before any mapping rule.
    minted = client.post(
        "/api/v1/api-keys",
        json={"name": "pbx", "scopes": ["meeting:read", "meeting:create"], "expires_in_days": 30},
        headers=_headers(owner),
        cookies=owner["cookies"],
    )
    assert minted.status_code == 201, minted.text
    bearer = {"Authorization": f"Bearer {minted.json()['key']}"}
    for response in (
        client.get(BASE, headers=bearer),
        client.put(CONSOLE, json=CUSTOMER_AGENT, headers=bearer),
        client.delete(CONSOLE, headers=bearer),
    ):
        assert response.status_code == 403, response.text
        assert _error(response) == "api_key_scope_denied"
    assert _settings_json(owner["workspace_id"]) == before


def test_writes_without_a_csrf_token_are_refused(client, email_outbox):
    owner = _tenant(client, email_outbox, "ch-csrf")
    no_csrf = {"X-Workspace-ID": owner["workspace_id"]}
    put = _put(client, owner, AGENT_CUSTOMER, headers=no_csrf)
    assert put.status_code == 403 and _error(put) == "csrf_failed"
    assert SETTINGS_KEY not in _settings_json(owner["workspace_id"])

    assert _put(client, owner, AGENT_CUSTOMER).status_code == 200  # allow-control
    delete = _delete(client, owner, headers=no_csrf)
    assert delete.status_code == 403 and _error(delete) == "csrf_failed"
    assert len(_get(client, owner).json()["items"]) == 1


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (CONSOLE, {"channels": []}),
        (CONSOLE, {"channels": [{"channel": 0, "role": "supervisor"}]}),
        (CONSOLE, {"channels": [{"channel": 0, "role": "unknown"}]}),
        (CONSOLE, {"channels": [{"channel": -1, "role": "agent"}]}),
        (CONSOLE, {"channels": [{"channel": 8, "role": "agent"}]}),
        (CONSOLE, {"channels": [{"channel": "0", "role": "agent"}]}),
        (CONSOLE, {"channels": [{"channel": True, "role": "agent"}]}),
        (CONSOLE, {"channels": [{"channel": 0.0, "role": "agent"}]}),
        (
            CONSOLE,
            {"channels": [{"channel": 0, "role": "agent"}, {"channel": 0, "role": "customer"}]},
        ),
        (CONSOLE, {"channels": [{"channel": 0, "role": "agent"}, {"channel": 1, "role": "agent"}]}),
        (CONSOLE, {"channels": [{"channel": 0, "role": "agent", "label": "x"}]}),
        (CONSOLE, {"channels": [{"channel": 0, "role": "agent"}], "version": 9}),
        (CONSOLE, {"channels": {"0": "agent"}}),
        (CONSOLE, [{"channel": 0, "role": "agent"}]),
        (CONSOLE, {}),
        (f"{BASE}/telepathy/console", AGENT_CUSTOMER),
        (f"{BASE}/file_upload/Console", AGENT_CUSTOMER),
        (f"{BASE}/file_upload/{'x' * 61}", AGENT_CUSTOMER),
    ],
)
def test_invalid_mappings_are_422(client, email_outbox, path, body):
    owner = _tenant(client, email_outbox, "ch-invalid")
    response = _put(client, owner, body, path=path)
    assert response.status_code == 422, response.text
    assert _error(response) == "invalid_channel_role_mapping"
    assert SETTINGS_KEY not in _settings_json(owner["workspace_id"])
    assert _audits("workspace.settings.updated") == []


def test_a_body_that_is_not_json_is_422(client, email_outbox):
    owner = _tenant(client, email_outbox, "ch-not-json")
    response = client.put(
        CONSOLE,
        content=b"{not json",
        headers={**_headers(owner), "Content-Type": "application/json"},
        cookies=owner["cookies"],
    )
    assert response.status_code == 422


def test_an_invalid_source_in_a_delete_is_422(client, email_outbox):
    owner = _tenant(client, email_outbox, "ch-invalid-delete")
    response = _delete(client, owner, f"{BASE}/telepathy/console")
    assert response.status_code == 422 and _error(response) == "invalid_channel_role_mapping"


# --- two workspaces (criterion 8) -----------------------------------------------------


def test_another_workspace_can_neither_read_nor_change_a_mapping(client, email_outbox):
    one = _tenant(client, email_outbox, "ch-tenant-one")
    two = _tenant(client, email_outbox, "ch-tenant-two")
    assert _put(client, one, AGENT_CUSTOMER).status_code == 200

    assert _get(client, two).json() == {"items": []}  # its own workspace: nothing
    assert _get(client, one).json()["items"][0]["source_provider"] == "console"  # allow-control
    hijack = {
        "X-Workspace-ID": one["workspace_id"],
        "X-CSRF-Token": two["cookies"][CSRF_COOKIE],
    }
    for response in (
        client.get(BASE, headers=hijack, cookies=two["cookies"]),
        client.put(CONSOLE, json=CUSTOMER_AGENT, headers=hijack, cookies=two["cookies"]),
        client.delete(CONSOLE, headers=hijack, cookies=two["cookies"]),
    ):
        assert response.status_code == 403
        assert _error(response) == "workspace_access_denied"
    assert _settings_json(one["workspace_id"])[SETTINGS_KEY]["file_upload:console"]["version"] == 1
    assert SETTINGS_KEY not in _settings_json(two["workspace_id"])


@pytest.mark.ffmpeg
def test_one_workspace_never_uses_another_workspace_mapping(
    client, email_outbox, monkeypatch, store, fixtures: Path
):
    _with_real_media(monkeypatch)
    one = _tenant(client, email_outbox, "ch-use-one")
    two = _tenant(client, email_outbox, "ch-use-two")
    assert _put(client, one, AGENT_CUSTOMER).status_code == 200

    first = _real_upload(client, one, fixtures / "distinct.flac", key="evt-use-one")
    second = _real_upload(client, two, fixtures / "distinct.flac", key="evt-use-two")
    assert first.status_code == 202 and second.status_code == 202, (first.text, second.text)
    trusted = _inspection(client, one, first.json()["meeting"]["id"])
    untrusted = _inspection(client, two, second.json()["meeting"]["id"])
    assert _roles(trusted) == [("agent", "trusted", None), ("customer", "trusted", None)]
    assert _roles(untrusted) == [("unknown", "unknown", "no_mapping")] * 2
    assert untrusted["mapping_version"] is None


# --- attribution at ingest (criteria 9 and 10) ----------------------------------------


@pytest.mark.ffmpeg
def test_uploads_are_attributed_with_the_mapping_of_their_own_source(
    client, email_outbox, monkeypatch, store, fixtures: Path
):
    _with_real_media(monkeypatch)
    owner = _tenant(client, email_outbox, "ch-attribution")

    def upload(name: str, key: str) -> dict:
        response = _real_upload(client, owner, fixtures / name, key=key)
        assert response.status_code == 202, response.text
        return _inspection(client, owner, response.json()["meeting"]["id"])

    # No mapping: the same audio stays unknown (allow-control below).
    no_mapping = upload("distinct.flac", "evt-attr-none")
    assert _roles(no_mapping) == [("unknown", "unknown", "no_mapping")] * 2

    # A mapping for another source of the workspace never applies.
    assert _put(client, owner, AGENT_CUSTOMER, path=f"{BASE}/file_upload/api").status_code == 200
    other_source = upload("distinct.flac", "evt-attr-other")
    assert _roles(other_source) == [("unknown", "unknown", "no_mapping")] * 2

    mapping = _put(client, owner, AGENT_CUSTOMER).json()
    distinct = upload("distinct.flac", "evt-attr-distinct")
    assert _roles(distinct) == [("agent", "trusted", None), ("customer", "trusted", None)]
    assert distinct["status"] == "complete"
    assert (distinct["mapping_source_type"], distinct["mapping_source_provider"]) == (
        "file_upload",
        "console",
    )
    assert (distinct["mapping_version"], distinct["mapping_digest"]) == (1, mapping["digest"])
    assert distinct["channel_layout"] == "stereo" and distinct["flags"] == []
    assert distinct["thresholds"] == {
        "frame_ms": 20,
        "active_threshold_dbfs": -50.0,
        "silent_max_active_share": 0.01,
        "duplicate_min_correlation": 0.99,
        "duplicate_max_lag_ms": 5.0,
        "narrowband_below_hz": 16000,
    }

    silent = upload("silent_right.flac", "evt-attr-silent")
    assert _roles(silent) == [("agent", "trusted", None), ("unknown", "unknown", "channel_silent")]
    assert silent["flags"] == ["silent_channel"]

    duplicated = upload("identical.flac", "evt-attr-identical")
    assert _roles(duplicated) == [("unknown", "unknown", "channels_duplicated")] * 2
    assert duplicated["flags"] == ["duplicated_channels", "mono_as_stereo"]

    mono = upload("mono.flac", "evt-attr-mono")
    assert _roles(mono) == [("unknown", "unknown", "mono")]

    narrow = upload("narrow.wav", "evt-attr-narrow")
    assert narrow["sample_rate_hz"] == 8000 and narrow["narrowband"] is True
    assert _roles(narrow) == [("agent", "trusted", None), ("customer", "trusted", None)]


@pytest.mark.ffmpeg
def test_a_mapping_changed_after_ingest_does_not_change_the_recording(
    client, email_outbox, monkeypatch, store, fixtures: Path
):
    _with_real_media(monkeypatch)
    owner = _tenant(client, email_outbox, "ch-history")
    first_mapping = _put(client, owner, AGENT_CUSTOMER).json()

    first = _real_upload(client, owner, fixtures / "distinct.flac", key="evt-hist-1")
    assert first.status_code == 202, first.text
    meeting_id = first.json()["meeting"]["id"]
    before = _inspection(client, owner, meeting_id)
    assert _roles(before) == [("agent", "trusted", None), ("customer", "trusted", None)]

    second_mapping = _put(client, owner, CUSTOMER_AGENT).json()
    assert second_mapping["version"] == 2
    after_change = _inspection(client, owner, meeting_id)
    assert after_change == before
    assert after_change["mapping_version"] == 1
    assert after_change["mapping_digest"] == first_mapping["digest"]

    assert _delete(client, owner).status_code == 204
    assert _inspection(client, owner, meeting_id) == before

    assert _put(client, owner, CUSTOMER_AGENT).status_code == 200
    later = _real_upload(client, owner, fixtures / "distinct.flac", key="evt-hist-2")
    assert later.status_code == 202, later.text
    now = _inspection(client, owner, later.json()["meeting"]["id"])
    assert _roles(now) == [("customer", "trusted", None), ("agent", "trusted", None)]
    assert now["mapping_version"] == 4


# --- unchanged behaviour (criterion 11) -----------------------------------------------

UPLOAD_KEYS = {"meeting", "recording", "job", "usage", "idempotent_replay", "ingestion"}
RECORDING_KEYS = {
    "id",
    "original_filename",
    "media_type",
    "container_format",
    "size_bytes",
    "checksum_sha256",
    "duration_seconds",
    "channel_count",
    "sample_rate_hz",
}


def test_without_the_column_store_the_detail_field_is_null(client, email_outbox, monkeypatch):
    # The production store before the column exists (SPEC §14): nothing is kept.
    previous = channel_store.use_store(channel_store.NoColumnStore())
    try:
        _patch_media(monkeypatch)
        owner = _tenant(client, email_outbox, "ch-null")
        assert _put(client, owner, AGENT_CUSTOMER).status_code == 200
        response = _upload(client, owner, key="evt-null-0001")
        assert response.status_code == 202, response.text
        assert set(response.json()) == UPLOAD_KEYS
        assert _inspection(client, owner, response.json()["meeting"]["id"]) is None
    finally:
        channel_store.use_store(previous)


def test_a_mocked_validator_upload_is_accepted_as_before_and_trusts_nothing(
    client, email_outbox, monkeypatch, store
):
    queued = _patch_media(monkeypatch)  # fake bytes: the analysis cannot run (R9)
    owner = _tenant(client, email_outbox, "ch-mocked")
    assert _put(client, owner, AGENT_CUSTOMER).status_code == 200
    response = _upload(client, owner, key="evt-mocked-0001")
    assert response.status_code == 202, response.text
    body = response.json()
    assert set(body) == UPLOAD_KEYS
    assert set(body["recording"]) == RECORDING_KEYS
    assert body["recording"]["channel_count"] == 1
    assert body["recording"]["sample_rate_hz"] == 16_000
    assert body["idempotent_replay"] is False
    assert len(queued) == 1

    inspection = _inspection(client, owner, body["meeting"]["id"])
    assert inspection["status"] == "unavailable"
    assert inspection["unavailable_reason"] in {"analysis_failed", "analysis_tool_unavailable"}
    assert _roles(inspection) == [("unknown", "unknown", "mono")]

    replay = _upload(client, owner, key="evt-mocked-0001")
    assert replay.status_code == 202 and replay.json()["idempotent_replay"] is True
    assert len(_kept_records(owner["workspace_id"])) == 1  # a replay writes no second record


def test_the_record_is_kept_with_the_original_media_object(
    client, email_outbox, monkeypatch, store
):
    _patch_media(monkeypatch)
    owner = _tenant(client, email_outbox, "ch-owned")
    response = _upload(client, owner, key="evt-owned-0001")
    assert response.status_code == 202, response.text
    with inspection_session_factory()() as db:
        recording = db.scalar(
            select(Recording).where(Recording.meeting_id == response.json()["meeting"]["id"])
        )
        media = db.get(MediaObject, recording.media_object_id)
        assert media.kind.value == "original"
    assert [item.id for item in _kept_records(owner["workspace_id"])] == [media.id]
    assert media.channel_inspection["version"] == "channel-inspection/1"


# --- voicemail ------------------------------------------------------------------------


def _import_voicemail(
    client, tenant: dict, *, external_id: str, audio: bytes, name: str, mime: str
):
    return client.post(
        "/api/v1/voicemails",
        headers=_headers(tenant),
        cookies=tenant["cookies"],
        data={"external_message_id": external_id, "caller_number": "0912 345 6789"},
        files={"file": (name, audio, mime)},
    )


def test_a_mocked_voicemail_import_is_accepted_as_before(client, email_outbox, monkeypatch, store):
    from backend.tests.test_uploads_api import _valid_metadata

    monkeypatch.setattr(
        "backend.voicemail.ingest.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=42.3),
    )
    monkeypatch.setattr("backend.voicemail.ingest.dispatch_job", lambda *_args: True)
    owner = _tenant(client, email_outbox, "ch-vm-mocked")
    response = _import_voicemail(
        client,
        owner,
        external_id="msg-ch-1",
        audio=b"voicemail wav bytes",
        name="vm.wav",
        mime="audio/wav",
    )
    assert response.status_code == 202, response.text
    assert set(response.json()) == {"voicemail", "job", "idempotent_replay"}
    inspection = _inspection(client, owner, response.json()["voicemail"]["meeting_id"])
    assert inspection["status"] == "unavailable"
    assert inspection["mapping_source_type"] == "voicemail"
    assert inspection["mapping_source_provider"] == "voicemail"


@pytest.mark.ffmpeg
def test_voicemail_uses_the_voicemail_source_mapping(
    client, email_outbox, monkeypatch, store, fixtures: Path
):
    monkeypatch.setattr("backend.voicemail.ingest.dispatch_job", lambda *_args: True)
    owner = _tenant(client, email_outbox, "ch-vm")
    assert _put(client, owner, AGENT_CUSTOMER).status_code == 200  # the console source only
    audio = (fixtures / "distinct.flac").read_bytes()

    unmapped = _import_voicemail(
        client, owner, external_id="msg-ch-2", audio=audio, name="vm.flac", mime="audio/flac"
    )
    assert unmapped.status_code == 202, unmapped.text
    first = _inspection(client, owner, unmapped.json()["voicemail"]["meeting_id"])
    assert _roles(first) == [("unknown", "unknown", "no_mapping")] * 2

    assert (
        _put(client, owner, CUSTOMER_AGENT, path=f"{BASE}/voicemail/voicemail").status_code == 200
    )
    mapped = _import_voicemail(
        client, owner, external_id="msg-ch-3", audio=audio, name="vm.flac", mime="audio/flac"
    )
    assert mapped.status_code == 202, mapped.text
    second = _inspection(client, owner, mapped.json()["voicemail"]["meeting_id"])
    assert _roles(second) == [("customer", "trusted", None), ("agent", "trusted", None)]
    assert second["mapping_source_type"] == "voicemail"


# --- turn 3 fixes ---------------------------------------------------------------------


def _set_settings_json(workspace_id: str, value) -> None:
    with inspection_session_factory()() as db:
        db.get(Workspace, workspace_id).settings_json = value
        db.commit()


@pytest.mark.parametrize(
    "value",
    [
        {SETTINGS_KEY: "garbage", "unrelated": {"kept": True}},
        {SETTINGS_KEY: [{"channel": 0, "role": "agent"}]},
        ["not", "an", "object"],
    ],
)
def test_an_unreadable_mapping_value_is_refused_not_replaced(client, email_outbox, value):
    # Finding 3a: a write must not silently replace the whole value.
    owner = _tenant(client, email_outbox, "ch-unreadable")
    _set_settings_json(owner["workspace_id"], value)

    for response in (_put(client, owner, AGENT_CUSTOMER), _delete(client, owner)):
        assert response.status_code == 409, response.text
        assert _error(response) == "channel_role_mappings_unreadable"
    assert _settings_json(owner["workspace_id"]) == value
    assert _audits("workspace.settings.updated") == []
    listed = _get(client, owner)
    assert listed.status_code == 200 and listed.json() == {"items": []}


def test_one_unreadable_entry_can_be_removed_and_replaced(client, email_outbox):
    # Finding 3b: an admin can clear a single broken entry; the others stay.
    owner = _tenant(client, email_outbox, "ch-bad-entry")
    assert _put(client, owner, AGENT_CUSTOMER, path=f"{BASE}/file_upload/api").status_code == 200
    settings_json = _settings_json(owner["workspace_id"])
    settings_json[SETTINGS_KEY]["file_upload:console"] = {
        "active": True,
        "version": 3,
        "channels": 7,
    }
    _set_settings_json(owner["workspace_id"], settings_json)

    listed = _get(client, owner)
    assert listed.status_code == 200
    assert [item["source_provider"] for item in listed.json()["items"]] == ["api"]

    removed = _delete(client, owner)
    assert removed.status_code == 204, removed.text
    event = _audits("workspace.settings.updated")[-1]
    assert event.details["change_kind"] == "removed"
    assert event.details["source_type"] == "file_upload"
    assert event.details["mapping_version"] == 4
    assert event.details["previous_version"] == 3
    assert event.details["previous_digest"] is None
    assert event.details["channels"] == []
    stored = _settings_json(owner["workspace_id"])[SETTINGS_KEY]
    assert stored["file_upload:console"]["active"] is False
    assert stored["file_upload:api"]["active"] is True

    assert _delete(client, owner).status_code == 404  # nothing active is left
    again = _put(client, owner, AGENT_CUSTOMER)
    assert again.status_code == 200 and again.json()["version"] == 5


@pytest.mark.parametrize("content", [b"null", b""], ids=["json-null", "empty"])
def test_a_null_or_empty_body_is_an_invalid_mapping(client, email_outbox, content):
    owner = _tenant(client, email_outbox, "ch-null-body")
    response = client.put(
        CONSOLE,
        content=content,
        headers={**_headers(owner), "Content-Type": "application/json"},
        cookies=owner["cookies"],
    )
    assert response.status_code == 422, response.text
    assert _error(response) == "invalid_channel_role_mapping"
    assert SETTINGS_KEY not in _settings_json(owner["workspace_id"])


@pytest.mark.parametrize("value", [{SETTINGS_KEY: "garbage"}, ["not", "an", "object"]])
@pytest.mark.ffmpeg
def test_unreadable_settings_never_fail_an_upload(
    client, email_outbox, monkeypatch, store, fixtures: Path, value
):
    # Finding 2a: the ingest request goes on; the channels are mapping_invalid.
    _with_real_media(monkeypatch)
    owner = _tenant(client, email_outbox, "ch-unreadable-upload")
    _set_settings_json(owner["workspace_id"], value)
    response = _real_upload(client, owner, fixtures / "distinct.flac", key="evt-unreadable-1")
    assert response.status_code == 202, response.text
    inspection = _inspection(client, owner, response.json()["meeting"]["id"])
    assert _roles(inspection) == [("unknown", "unknown", "mapping_invalid")] * 2


def test_the_upload_inspection_runs_off_the_event_loop(client, email_outbox, monkeypatch, store):
    # Finding 4: the analysis must not block the event loop of the async upload route.
    import asyncio

    from backend.media import inspect_channels as real_inspect

    _patch_media(monkeypatch)
    seen: list[bool] = []

    def recording_inspect(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            seen.append(True)  # called on the event loop thread
        except RuntimeError:
            seen.append(False)  # called from a worker thread
        return real_inspect(*args, **kwargs)

    monkeypatch.setattr(meetings_router, "inspect_channels", recording_inspect)
    owner = _tenant(client, email_outbox, "ch-threadpool")
    response = _upload(client, owner, key="evt-threadpool-1")
    assert response.status_code == 202, response.text
    assert seen == [False]
