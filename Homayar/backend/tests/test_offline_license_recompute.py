"""HOM-109 Amendment 3 item 3: a manual analysis recompute is new processing.

Restricted licence: ``POST /meetings/{id}/analysis/recompute`` answers 403
``license_restricted`` and records or dispatches nothing. Allow-control: the
same stale meeting under a valid licence gets today's 202. The licence files
are replaced in place between the two calls, as an operator renewal would.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import reset_settings_cache
from backend.database.saas_models import AnalysisRun
from backend.jobs import recompute
from backend.licensing.offline_license import clear_license_cache
from backend.services.audit_event_types import AuditEventType
from backend.tests.test_analysis_recompute import (
    _audit,
    _bundle,
    _correct,
    _manual,
    _no_recompute_on_correction,
    _processed_meeting,
    _snapshot,
)
from backend.tests.test_offline_license import (
    _expired_doc,
    _key,
    _now_doc,
    _trust_dir,
    _trust_key,
    _write_licence,
)


@pytest.fixture(autouse=True)
def _isolated_licence_state(monkeypatch: pytest.MonkeyPatch):
    clear_license_cache()
    yield
    clear_license_cache()
    reset_settings_cache()


@pytest.fixture
def licence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """A valid licence on disk; env points at it (``reserve_usage`` reads ``get_settings``)."""
    folder = tmp_path / "licence"
    folder.mkdir()
    key = _key()
    trust = _trust_dir(folder)
    _trust_key(trust, key)
    license_file, _ = _write_licence(folder, _now_doc(), key)
    monkeypatch.setenv("OFFLINE_LICENSE_FILE", str(license_file))
    monkeypatch.setenv("OFFLINE_LICENSE_TRUST_DIR", str(trust))
    reset_settings_cache()
    return {"folder": folder, "key": key, "file": license_file, "trust": trust}


@pytest.fixture
def settings(settings, licence):
    # The recompute builds its providers from the settings, like the worker.
    return settings.model_copy(
        update={
            "allow_mock_providers": True,
            "asr_provider": "mock",
            "diarization_provider": "mock",
            "intelligence_provider": "mock",
            "offline_license_file": str(licence["file"]),
            "offline_license_trust_dir": str(licence["trust"]),
        }
    )


@pytest.fixture
def analysis_provider(monkeypatch):
    monkeypatch.setattr(recompute, "create_provider_bundle", lambda _settings: _bundle())


def test_a_restricted_licence_refuses_the_manual_recompute_and_writes_nothing(
    client, email_outbox, settings, monkeypatch, analysis_provider, licence
):
    _no_recompute_on_correction(settings, monkeypatch)
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    _correct(client, workspace_id, meeting_id)
    runs = _snapshot(AnalysisRun, meeting_id)
    dispatched: list[str] = []
    real_dispatch = recompute.dispatch_recompute

    def counting_dispatch(meeting, revision, **kwargs):
        dispatched.append(meeting)
        return real_dispatch(meeting, revision, **kwargs)

    monkeypatch.setattr(recompute, "dispatch_recompute", counting_dispatch)

    _write_licence(licence["folder"], _expired_doc(), licence["key"])
    refused = _manual(client, workspace_id, meeting_id)
    assert refused.status_code == 403, refused.text
    error = refused.json()["error"]
    assert error["code"] == "license_restricted"
    assert error["message"] == "The offline licence does not allow new processing"
    assert error["details"] == {"state": "expired", "reason": None}
    assert _audit(meeting_id) == []
    assert _snapshot(AnalysisRun, meeting_id) == runs
    assert dispatched == []

    # Allow-control: the renewed (valid) licence, same stale meeting, today's answer.
    _write_licence(licence["folder"], _now_doc(), licence["key"])
    accepted = _manual(client, workspace_id, meeting_id)
    assert accepted.status_code == 202, accepted.text
    assert accepted.json() == {"status": "up_to_date"}
    assert dispatched == [meeting_id]
    assert len(_snapshot(AnalysisRun, meeting_id)) == len(runs) + 1
    assert [event.event_type for event in _audit(meeting_id)] == [
        AuditEventType.MEETING_RECOMPUTE_REQUESTED.value,
        AuditEventType.MEETING_RECOMPUTE_COMPLETED.value,
    ]
