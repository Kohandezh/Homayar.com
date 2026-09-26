"""WP08-T02: workspace AI processing policies and per-attempt snapshots (ADR 0027).

A policy is versioned and append-only per workspace, changed only by the
owner with CSRF, a reason and the version that was read; every processing
attempt records the policy version and model placement it ran under before
any stage runs, and a retry reads the then-current version.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    AiPolicyVersion,
    AuditEvent,
    PolicyRecordImmutableError,
    RunPolicySnapshot,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs.processor import process_job
from backend.providers.base import ProviderError
from backend.providers.mocks import MockASRProvider
from backend.providers.policy import DEFAULT_POLICY, PRESETS, AiPolicy
from backend.providers.registry import STAGES, EndpointKind
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _workspace_with_roles
from backend.tests.test_ingestion_ledger import _get, _headers, _patch_media, _tenant, _upload
from backend.tests.test_job_failure_policy import _make_due
from backend.tests.test_job_processor import _providers

POLICY_URL = "/api/v1/admin/ai-policy"
LOCAL = PRESETS["local_only"].document()


def _cloud_intelligence(*regions: str) -> dict:
    return {
        **DEFAULT_POLICY.document(),
        "intelligence": {
            "endpoint_kinds": ["cloud", "in_process", "private"],
            "regions": list(regions),
            "manifests": [],
            "fallback": False,
            "cloud_text": "redacted",
        },
    }


def _put(persona, body: dict):
    return persona.client.put(
        POLICY_URL, json=body, cookies=persona.cookies, headers=persona.headers
    )


def _put_tenant(client, tenant: dict, body: dict):
    return client.put(POLICY_URL, json=body, headers=_headers(tenant), cookies=tenant["cookies"])


# --- the policy document -------------------------------------------------------------


@pytest.mark.parametrize(
    ("stage", "message"),
    [
        ({"endpoint_kinds": ["cloud"]}, "approved regions"),
        ({"endpoint_kinds": ["in_process", "in_process"]}, "must not repeat"),
        ({"endpoint_kinds": ["private"], "regions": ["unknown"]}, "invalid region"),
        ({"endpoint_kinds": ["private"], "regions": ["EU West"]}, "invalid region"),
        ({"endpoint_kinds": ["private"], "manifests": ["Bad Id"]}, "invalid manifest id"),
        ({"endpoint_kinds": ["private"], "api_key": "sk-test"}, "Extra inputs"),
        ({"endpoint_kinds": ["private"], "endpoint": "https://llm.example"}, "Extra inputs"),
        # Audio stages have no text form: redacted text never licenses audio.
        ({"endpoint_kinds": ["in_process"], "cloud_text": "original"}, "Extra inputs"),
        ({"endpoint_kinds": ["satellite"]}, "endpoint_kinds"),
    ],
)
def test_stage_policies_are_validated(stage, message):
    with pytest.raises(ValidationError, match=message):
        AiPolicy.model_validate({**LOCAL, "asr": stage})


def test_the_digest_is_canonical_and_the_default_allows_no_cloud():
    first = AiPolicy.model_validate(_cloud_intelligence("eu-central", "ir-thr"))
    reordered = _cloud_intelligence("ir-thr", "eu-central")
    reordered["intelligence"]["endpoint_kinds"] = ["private", "cloud", "in_process"]
    assert AiPolicy.model_validate(reordered).digest == first.digest
    assert first.digest != DEFAULT_POLICY.digest
    for stage in STAGES:
        kinds = DEFAULT_POLICY.stage(stage).endpoint_kinds
        assert EndpointKind.CLOUD not in kinds and EndpointKind.IN_PROCESS in kinds
    assert DEFAULT_POLICY.intelligence.cloud_text == "redacted"
    for stage in STAGES:
        assert PRESETS["local_only"].stage(stage).endpoint_kinds == [EndpointKind.IN_PROCESS]


# --- the API ---------------------------------------------------------------------------


def test_policy_versions_are_owner_only_optimistic_and_audited(client, email_outbox, monkeypatch):
    owner, personas, _ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"admin": "admin", "member": "member"}
    )
    shown = owner.get(POLICY_URL)
    assert shown.status_code == 200, shown.text
    assert shown.json()["version"] == 0 and shown.json()["source"] == "default"
    assert shown.json()["policy"] == DEFAULT_POLICY.document()
    assert shown.json()["digest"] == DEFAULT_POLICY.digest
    assert personas["admin"].get(POLICY_URL).status_code == 200
    assert personas["member"].get(POLICY_URL).status_code == 403

    body = {"policy": LOCAL, "expected_version": 0, "reason": "air-gapped pilot"}
    denied = _put(personas["admin"], body)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "permission_denied"
    key = _mint(client, owner.headers, owner.cookies, scopes=("meeting:read",))
    by_key = client.put(
        POLICY_URL,
        json=body,
        headers=_bearer(key.json()["key"], **{"X-Workspace-ID": owner.workspace_id}),
    )
    assert by_key.status_code == 403
    no_csrf = client.put(
        POLICY_URL, json=body, cookies=owner.cookies, headers={"X-Workspace-ID": owner.workspace_id}
    )
    assert no_csrf.status_code == 403 and no_csrf.json()["error"]["code"] == "csrf_failed"

    created = _put(owner, body)
    assert created.status_code == 200, created.text
    assert created.json()["changed"] is True
    assert created.json()["current"]["version"] == 1
    assert created.json()["current"]["source"] == "workspace"
    assert created.json()["current"]["policy"] == LOCAL
    assert created.json()["current"]["reason"] == "air-gapped pilot"

    same = _put(owner, {**body, "expected_version": 1})
    assert same.status_code == 200 and same.json()["changed"] is False
    assert same.json()["current"]["version"] == 1

    stale = _put(
        owner, {"policy": DEFAULT_POLICY.document(), "expected_version": 0, "reason": "x y"}
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "policy_version_conflict"
    assert stale.json()["error"]["details"] == {"current_version": 1}

    invalid = _put(
        owner,
        {
            "policy": {**LOCAL, "intelligence": {"endpoint_kinds": ["cloud"]}},
            "expected_version": 1,
            "reason": "cloud please",
        },
    )
    assert invalid.status_code == 422
    short_reason = _put(owner, {**body, "expected_version": 1, "reason": "x"})
    assert short_reason.status_code == 422

    history = owner.get(f"{POLICY_URL}/versions").json()
    assert history["current_version"] == 1
    assert [item["version"] for item in history["items"]] == [1]
    assert set(history["presets"]) == {"local_only", "private"}

    with inspection_session_factory()() as db:
        events = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type == "ai_policy.updated",
                    AuditEvent.workspace_id == owner.workspace_id,
                )
            )
        )
    assert len(events) == 1  # the unchanged PUT is not a new version
    assert events[0].details["version"] == 1 and events[0].details["previous_version"] == 0
    assert events[0].details["stages"] == "asr,diarization,intelligence"
    assert events[0].details["reason"] == "air-gapped pilot"


# --- snapshots ---------------------------------------------------------------------------


class _OnceUnavailableASR(MockASRProvider):
    def __init__(self) -> None:
        super().__init__(allow_mock=True, transcript="پیگیری قرارداد انجام شود")
        self.calls = 0

    async def transcribe(self, request):
        self.calls += 1
        if self.calls == 1:
            raise ProviderError("asr_unavailable", "ASR is unavailable", retryable=True)
        return await super().transcribe(request)


def test_every_attempt_records_the_policy_it_ran_under(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "policy-snapshots")
    accepted = _upload(client, tenant, key="policy-snapshot-1")
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job"]["id"]
    meeting_id = accepted.json()["meeting"]["id"]
    providers = _providers(_OnceUnavailableASR())

    first = process_job(job_id, worker_id="w-1", settings=settings, providers=providers)
    assert first["status"] == "retry_scheduled", first
    # The owner tightens the policy between the attempts: the retry reads it.
    changed = _put_tenant(
        client, tenant, {"policy": LOCAL, "expected_version": 0, "reason": "local only now"}
    )
    assert changed.status_code == 200, changed.text
    _make_due(job_id)
    second = process_job(job_id, worker_id="w-2", settings=settings, providers=providers)
    assert second["status"] == "completed", second

    listed = _get(client, tenant, f"/api/v1/admin/jobs/{job_id}/policy-snapshots")
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert [item["attempt"] for item in items] == [1, 2]
    assert [item["policy_version"] for item in items] == [0, 1]
    assert [item["policy_digest"] for item in items] == [
        DEFAULT_POLICY.digest,
        PRESETS["local_only"].digest,
    ]
    assert items[1]["policy_document"] == LOCAL
    for item in items:
        assert item["text_privacy_digest"] is None and item["strict_manifests"] is False
        assert [entry["stage"] for entry in item["stages"]] == [
            "asr",
            "diarization",
            "intelligence",
        ]
        for entry in item["stages"]:
            # Test doubles have no manifest: honestly unregistered, and without
            # an endpoint they are placed in-process (inferred, not declared).
            assert entry["manifest_status"] == "unregistered"
            assert entry["endpoint_kind"] == "in_process" and entry["placement"] == "inferred"
        blob = json.dumps(item).lower()
        assert "api_key" not in blob and "secret" not in blob and "bearer" not in blob

    # Stage runs expose their manifest (WP08-T01); a stage never reached in
    # attempt 1 ran under none.
    job = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/job").json()
    by_attempt = {
        (stage["attempt"], stage["stage"]): stage["manifest_id"] for stage in job["stages"]
    }
    assert by_attempt[(1, "asr")] == "unregistered" and by_attempt[(1, "intelligence")] is None
    assert {by_attempt[(2, stage)] for stage in STAGES} == {"unregistered"}


def test_policies_and_snapshots_are_tenant_scoped(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    first = _tenant(client, email_outbox, "policy-a")
    second = _tenant(client, email_outbox, "policy-b")
    assert (
        _put_tenant(
            client, first, {"policy": LOCAL, "expected_version": 0, "reason": "tenant a only"}
        ).status_code
        == 200
    )
    accepted = _upload(client, first, key="policy-scope-1")
    job_id = accepted.json()["job"]["id"]
    assert (
        process_job(job_id, worker_id="w", settings=settings, providers=_providers())["status"]
        == "completed"
    )

    seen_by_b = _get(client, second, POLICY_URL).json()
    assert seen_by_b["version"] == 0 and seen_by_b["source"] == "default"
    foreign = _get(client, second, f"/api/v1/admin/jobs/{job_id}/policy-snapshots")
    assert foreign.status_code == 404 and foreign.json()["error"]["code"] == "job_not_found"

    # Row-level (PostgreSQL): a session bound to tenant B sees none of tenant
    # A's rows, even when it asks by id.
    with get_session_factory()() as db:
        if db.get_bind().dialect.name != "postgresql":
            return
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        assert db.scalars(select(AiPolicyVersion)).all() == []
        assert (
            db.scalars(select(RunPolicySnapshot).where(RunPolicySnapshot.job_id == job_id)).all()
            == []
        )


def test_policy_rows_are_append_only(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "policy-append-only")
    assert (
        _put_tenant(
            client, tenant, {"policy": LOCAL, "expected_version": 0, "reason": "append only"}
        ).status_code
        == 200
    )
    accepted = _upload(client, tenant, key="policy-append-1")
    process_job(
        accepted.json()["job"]["id"], worker_id="w", settings=settings, providers=_providers()
    )

    factory = get_session_factory()
    for model in (AiPolicyVersion, RunPolicySnapshot):
        with factory() as db:
            bind_tenant_context(db, workspace_id=tenant["workspace_id"])
            row = db.scalars(select(model)).one()
            if model is AiPolicyVersion:
                row.reason = "rewritten"
            else:
                row.policy_version = 7
            with pytest.raises(PolicyRecordImmutableError):
                db.flush()
            db.rollback()
            db.delete(db.scalars(select(model)).one())
            with pytest.raises(PolicyRecordImmutableError):
                db.flush()
            db.rollback()

    with factory() as db:
        if db.get_bind().dialect.name != "postgresql":
            return
        # Beneath the ORM: the application role holds no UPDATE/DELETE grant.
        for statement in (
            "UPDATE ai_policy_versions SET reason = 'rewritten'",
            "DELETE FROM run_policy_snapshots",
            "UPDATE model_manifests SET status = 'qualified'",
        ):
            bind_tenant_context(db, workspace_id=tenant["workspace_id"])
            with pytest.raises(DBAPIError, match="permission denied"):
                db.execute(text(statement))
            db.rollback()
