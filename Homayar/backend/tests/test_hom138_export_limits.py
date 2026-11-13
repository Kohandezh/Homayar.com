"""HOM-138 (F70, D5, A8, A13): the export size limit.

The "export text size" is the sum of ``len()`` of every string the export
would carry, measured before anything is rendered.  Over
``export_max_text_chars`` every export route answers ``413
export_too_large`` with the limit in ``error.details.limit``, renders
nothing and audits no export.  Exactly at the limit the export succeeds.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from backend.config import Settings
from backend.database.saas_models import AuditEvent
from backend.routers.v1 import insights as insights_router
from backend.routers.v1 import reports as reports_router
from backend.routers.v1 import shares as shares_router
from backend.services.export_service import export_text_size
from backend.services.report_export_service import report_export_payload, report_export_text_size
from backend.tests._database import inspection_session_factory
from backend.tests.test_insights_shares_api import _processed_meeting, _write_headers
from backend.tests.test_report_export_service import _provenance, _report
from backend.tests.test_share_policies import _cookies, _share, _token

REPORT_FORMATS = ["pdf", "csv", "json"]
TRANSCRIPT_FORMATS = ["txt", "json", "srt", "vtt"]


# --- the measure ---------------------------------------------------------------


@dataclass
class _Leaf:
    text: str
    count: int
    items: list[object] = field(default_factory=list)


def test_export_text_size_counts_string_values_only():
    value = {
        "title": "سلام",  # 4
        "nested": {"a": ["ab", 3, None, True, {"key-not-counted": "xyz"}]},  # 2 + 3
        "dataclass": _Leaf(text="12345", count=9, items=["x", ("yy",)]),  # 5 + 1 + 2
        "empty": "",
    }
    assert export_text_size(value) == 4 + 2 + 3 + 5 + 1 + 2


def _string_leaves(value) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(_string_leaves(item) for item in value.values())
    if isinstance(value, list):
        return sum(_string_leaves(item) for item in value)
    return 0


@pytest.mark.parametrize("format_name", REPORT_FORMATS)
def test_report_export_text_size_is_the_string_leaves_of_the_json_payload(format_name):
    report, provenance = _report(), _provenance(format=format_name)
    expected = _string_leaves(report_export_payload(report, provenance))
    assert report_export_text_size(report, provenance) == expected
    assert expected > 0


# --- the setting -------------------------------------------------------------


def test_the_setting_defaults_to_two_million_and_reads_the_environment(monkeypatch):
    assert Settings(_env_file=None).export_max_text_chars == 2_000_000
    monkeypatch.setenv("EXPORT_MAX_TEXT_CHARS", "1234")
    assert Settings(_env_file=None).export_max_text_chars == 1234
    assert Settings(_env_file=None, export_max_text_chars=1).export_max_text_chars == 1


@pytest.mark.parametrize("bad", [0, -1, -2_000_000])
def test_the_setting_rejects_zero_and_negatives(bad):
    with pytest.raises(ValidationError, match="EXPORT_MAX_TEXT_CHARS"):
        Settings(_env_file=None, export_max_text_chars=bad)


# --- the routes ----------------------------------------------------------------


class _Measured:
    """Records the size each export route measured (wraps the real measure)."""

    def __init__(self, monkeypatch) -> None:
        self.sizes: list[int] = []

        def spy(measure):
            def wrapped(*args, **kwargs):
                size = measure(*args, **kwargs)
                self.sizes.append(size)
                return size

            return wrapped

        monkeypatch.setattr(insights_router, "export_text_size", spy(export_text_size))
        monkeypatch.setattr(shares_router, "export_text_size", spy(export_text_size))
        monkeypatch.setattr(reports_router, "report_export_text_size", spy(report_export_text_size))

    def last(self) -> int:
        assert self.sizes, "the route measured nothing"
        return self.sizes[-1]


def _exported_count(meeting_id: str) -> int:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == "meeting.exported", AuditEvent.target_id == meeting_id)
        )


def _share_access_count() -> int:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == "meeting.share_accessed")
        )


def _too_large(response, limit: int) -> None:
    assert response.status_code == 413, response.text
    error = response.json()["error"]
    assert error["code"] == "export_too_large"
    assert error["details"] == {"limit": limit}
    assert response.headers["cache-control"] == "no-store"
    assert "content-disposition" not in response.headers


def _assert_limit_applies(get, measured, settings, monkeypatch, meeting_id, *, audit_count):
    """Measure once, then: one below the size is 413 with no audit, the size is 200."""
    monkeypatch.setattr(settings, "export_max_text_chars", 2_000_000)
    first = get()
    assert first.status_code == 200, first.text
    size = measured.last()
    assert size > 1
    before = audit_count()
    monkeypatch.setattr(settings, "export_max_text_chars", size - 1)
    _too_large(get(), size - 1)
    assert audit_count() == before
    # allow-control: exactly at the limit is allowed
    monkeypatch.setattr(settings, "export_max_text_chars", size)
    ok = get()
    assert ok.status_code == 200, ok.text
    assert measured.last() == size
    assert audit_count() == before + 1
    assert ok.headers["cache-control"] == "no-store"
    assert ok.headers["content-disposition"].startswith("attachment; ")


@pytest.mark.parametrize("format_name", TRANSCRIPT_FORMATS)
def test_transcript_export_over_the_limit_is_413(
    client, email_outbox, settings, monkeypatch, format_name
):
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    measured = _Measured(monkeypatch)

    def get():
        return client.get(
            f"/api/v1/meetings/{meeting_id}/export",
            headers={"X-Workspace-ID": workspace_id},
            params={"format": format_name},
        )

    _assert_limit_applies(
        get,
        measured,
        settings,
        monkeypatch,
        meeting_id,
        audit_count=lambda: _exported_count(meeting_id),
    )


@pytest.mark.parametrize("format_name", REPORT_FORMATS)
def test_report_export_over_the_limit_is_413(
    client, email_outbox, settings, monkeypatch, format_name
):
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    measured = _Measured(monkeypatch)

    def get():
        return client.get(
            f"/api/v1/meetings/{meeting_id}/report/export",
            headers={"X-Workspace-ID": workspace_id},
            params={"format": format_name},
        )

    _assert_limit_applies(
        get,
        measured,
        settings,
        monkeypatch,
        meeting_id,
        audit_count=lambda: _exported_count(meeting_id),
    )


@pytest.mark.parametrize("format_name", TRANSCRIPT_FORMATS)
def test_share_export_over_the_limit_is_413(
    client, email_outbox, settings, monkeypatch, format_name
):
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    cookies = _cookies(client)
    headers = _write_headers(client, workspace_id)
    created = _share(client, headers, cookies, meeting_id, audience="workspace", allow_export=True)
    assert created.status_code == 201, created.text
    token = _token(created.json())
    measured = _Measured(monkeypatch)

    def get():
        return client.get(
            f"/api/v1/shares/{token}/export", params={"format": format_name}, cookies=cookies
        )

    _assert_limit_applies(
        get, measured, settings, monkeypatch, meeting_id, audit_count=_share_access_count
    )


def test_the_transcript_text_counts_toward_the_limit(client, email_outbox, settings, monkeypatch):
    """The measure grows with the transcript: a longer segment text is a larger size."""
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    measured = _Measured(monkeypatch)
    headers = {"X-Workspace-ID": workspace_id}
    path = f"/api/v1/meetings/{meeting_id}/export"
    assert client.get(path, headers=headers, params={"format": "txt"}).status_code == 200
    transcript = client.get(f"/api/v1/meetings/{meeting_id}/transcript", headers=headers).json()
    segment_chars = sum(len(segment["text"]) for segment in transcript["segments"])
    assert segment_chars > 0
    assert measured.last() >= segment_chars + len(transcript["segments"][0]["speaker_name"])
