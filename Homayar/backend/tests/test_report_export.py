"""HOM-137 (F70): report exports with version, provenance and ACL controls.

``GET /meetings/{id}/report/export`` renders the call report as PDF, CSV or
JSON.  It answers exactly like the transcript export for every refused
caller (permission, record scope, tombstone, privacy view), its JSON
``report`` equals ``GET /report`` for the same caller and view, both export
routes pin ``revision_id`` with a 409, and every export carries the export
version and the transcript revision.
"""

from __future__ import annotations

import csv
import io
from urllib.parse import urlparse

from sqlalchemy import select

from backend.database.saas_models import AuditEvent, TranscriptRevision
from backend.providers.base import IntelligenceProvider, ProviderIdentity
from backend.providers.factory import ProviderBundle
from backend.providers.schemas import IntelligenceInsight, IntelligenceRequest, IntelligenceResult
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _insert_meeting, _workspace_with_roles
from backend.tests.test_call_report import GROUNDED, _bundle, _RealASR, _RealDiarization
from backend.tests.test_insights_shares_api import _write_headers
from backend.tests.test_privacy_routing import (
    _no_raw,
    _owner_tenant,
    _patch_media,
    _process,
    _set_policy,
)

STALE_REVISION = "00000000-0000-0000-0000-000000000000"


def current_revision_id(meeting_id: str) -> str:
    with inspection_session_factory()() as db:
        revision = db.scalar(
            select(TranscriptRevision).where(
                TranscriptRevision.meeting_id == meeting_id,
                TranscriptRevision.is_current.is_(True),
            )
        )
    assert revision is not None, meeting_id
    return revision.id


class _SummaryIntelligence(IntelligenceProvider):
    """A fixed non-mock analysis whose summary starts with formula characters."""

    def __init__(self, summary: str) -> None:
        self._summary = summary

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="report_intelligence", model="report-v1")

    async def analyze(self, request: IntelligenceRequest) -> IntelligenceResult:
        return IntelligenceResult(
            provider="report_intelligence",
            model="report-v1",
            summary=self._summary,
            summary_source_turn_indexes=[0],
            action_items=[],
            insights=[
                IntelligenceInsight(
                    category="decision",
                    description="@SUM(A1) به عنوان یافته",
                    source_turn_indexes=[0],
                )
            ],
            is_mock=False,
        )


def _summary_bundle(summary: str) -> ProviderBundle:
    return ProviderBundle(
        asr=_RealASR(), diarization=_RealDiarization(), intelligence=_SummaryIntelligence(summary)
    )


def _get(client, workspace_id: str, path: str, **params):
    return client.get(path, headers={"X-Workspace-ID": workspace_id}, params=params)


def _export(client, workspace_id: str, meeting_id: str, **params):
    return _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/report/export", **params)


def _exported_audits(meeting_id: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type == "meeting.exported",
                    AuditEvent.target_id == meeting_id,
                )
            )
        )


def _report_exported_audits(meeting_id: str) -> list[AuditEvent]:
    return [
        event for event in _exported_audits(meeting_id) if event.details.get("artifact") == "report"
    ]


# --- AC1: JSON ----------------------------------------------------------------


def test_json_export_equals_the_report_and_carries_provenance(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _bundle_processed(client, email_outbox, settings, monkeypatch)
    report = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/report")
    assert report.status_code == 200, report.text

    exported = _export(client, workspace_id, meeting_id, format="json")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("application/json")
    assert (
        exported.headers["content-disposition"]
        == f'attachment; filename="homayar-report-{meeting_id}.json"'
    )
    assert exported.headers["cache-control"] == "no-store"
    assert exported.headers["x-homayar-export-version"] == "1"
    assert exported.headers["x-homayar-transcript-revision"] == current_revision_id(meeting_id)

    payload = exported.json()
    assert payload["export_version"] == 1
    assert payload["report"] == report.json()
    provenance = payload["provenance"]
    assert provenance["export_version"] == 1
    assert provenance["artifact"] == "report"
    assert provenance["report_version"] == 1
    assert provenance["meeting_id"] == meeting_id
    assert provenance["transcript_revision"]["id"] == current_revision_id(meeting_id)
    assert provenance["transcript_revision"]["revision_number"] == 1
    assert provenance["analysis"]["run_id"] == report.json()["summary"]["analysis_run_id"]
    assert provenance["analysis"]["provider"] == "report_intelligence"
    assert provenance["analysis"]["model"] == "report-v1"
    assert provenance["analysis"]["transcript_revision_id"] == current_revision_id(meeting_id)
    assert provenance["privacy"]["view"] == "original"
    assert provenance["privacy"]["policy_enforced"] is False
    # unicode is not escaped away
    assert "جلسهٔ بعد سه‌شنبه" in exported.content.decode("utf-8")


def _bundle_processed(client, email_outbox, settings, monkeypatch, bundle=None):
    from backend.tests.test_evidence_spans import _processed

    return _processed(
        client, email_outbox, settings, monkeypatch, providers=bundle or _bundle(GROUNDED)
    )


# --- AC2: CSV -----------------------------------------------------------------


def test_csv_export_media_type_bom_rows_and_formula_neutralization(
    client, email_outbox, settings, monkeypatch
):
    summary = '=HYPERLINK("http://x") قیمت فردا'
    _, workspace_id, meeting_id = _bundle_processed(
        client, email_outbox, settings, monkeypatch, bundle=_summary_bundle(summary)
    )
    exported = _export(client, workspace_id, meeting_id, format="csv")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "text/csv; charset=utf-8"
    assert (
        exported.headers["content-disposition"]
        == f'attachment; filename="homayar-report-{meeting_id}.csv"'
    )
    content = exported.content
    assert content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"), newline="")))
    assert rows[0] == ["path", "value"]
    cells = {path: value for path, value in rows[1:]}
    assert cells["report.meeting_id"] == meeting_id
    assert cells["export_version"] == "1"
    # the formula-looking summary and the "@SUM" finding text are prefixed
    assert cells["report.summary.overview"] == "'" + summary
    assert cells["report.qa.findings.0.text"] == "'" + summary
    assert cells["report.outcome.decisions.0.text"] == "'@SUM(A1) به عنوان یافته"
    # every remaining string cell that still starts a formula would be a leak
    formula_starts = {"=", "+", "-", "@", "＝", "＋", "－", "＠"}
    for value in cells.values():
        assert value.lstrip()[:1] not in formula_starts or value.startswith("'")


# --- AC3: PDF -----------------------------------------------------------------


def test_pdf_export_is_a_pdf_with_provenance_text(client, email_outbox, settings, monkeypatch):
    _, workspace_id, meeting_id = _bundle_processed(client, email_outbox, settings, monkeypatch)
    exported = _export(client, workspace_id, meeting_id, format="pdf")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "application/pdf"
    assert (
        exported.headers["content-disposition"]
        == f'attachment; filename="homayar-report-{meeting_id}.pdf"'
    )
    content = exported.content
    assert content.startswith(b"%PDF-")
    assert content.rstrip().endswith(b"%%EOF")
    assert b"Export version: 1" in content
    assert current_revision_id(meeting_id).encode() in content


def test_unknown_or_missing_format_is_refused(client, email_outbox, settings, monkeypatch):
    _, workspace_id, meeting_id = _bundle_processed(client, email_outbox, settings, monkeypatch)
    assert _export(client, workspace_id, meeting_id, format="xml").status_code == 422
    assert _export(client, workspace_id, meeting_id).status_code == 422


# --- AC8: audit ---------------------------------------------------------------


def test_each_successful_report_export_writes_exactly_one_audit(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _bundle_processed(client, email_outbox, settings, monkeypatch)
    assert _exported_audits(meeting_id) == []  # reading and processing export nothing
    report = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/report")
    assert report.status_code == 200
    assert _exported_audits(meeting_id) == []

    first = _export(client, workspace_id, meeting_id, format="json")
    assert first.status_code == 200, first.text
    (event,) = _report_exported_audits(meeting_id)
    assert event.details == {"artifact": "report", "format": "json", "view": "original"}

    second = _export(client, workspace_id, meeting_id, format="pdf")
    assert second.status_code == 200, second.text
    events = _report_exported_audits(meeting_id)
    assert len(events) == 2
    assert {event.details["format"] for event in events} == {"json", "pdf"}


# --- AC6: revision pinning ----------------------------------------------------


def test_revision_mismatch_is_409_on_both_export_routes_without_audit(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _bundle_processed(client, email_outbox, settings, monkeypatch)
    current = current_revision_id(meeting_id)
    exported_before: list[str] = []
    for path in (
        f"/api/v1/meetings/{meeting_id}/export",
        f"/api/v1/meetings/{meeting_id}/report/export",
    ):
        ok = _get(client, workspace_id, path, format="json", revision_id=current)
        assert ok.status_code == 200, (path, ok.text)
    exported_before = [event.details for event in _exported_audits(meeting_id)]
    assert len(exported_before) == 2  # one per successful export

    for path in (
        f"/api/v1/meetings/{meeting_id}/export",
        f"/api/v1/meetings/{meeting_id}/report/export",
    ):
        stale = _get(client, workspace_id, path, format="json", revision_id=STALE_REVISION)
        assert stale.status_code == 409, (path, stale.text)
        assert stale.json()["error"]["code"] == "transcript_revision_mismatch"
    # the 409s exported nothing and audited nothing new
    assert [event.details for event in _exported_audits(meeting_id)] == exported_before

    absent = _export(client, workspace_id, meeting_id, format="json")
    assert absent.status_code == 200
    assert len(_exported_audits(meeting_id)) == 3


def _evidence_refs(node) -> list[dict]:
    """Every evidence reference in a report body, wherever it sits."""
    found: list[dict] = []
    if isinstance(node, dict):
        if "is_current_revision" in node:
            found.append(node)
        for value in node.values():
            found.extend(_evidence_refs(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_evidence_refs(item))
    return found


def test_report_export_uses_the_one_revision_row_it_checked(
    client, email_outbox, settings, monkeypatch
):
    """Amendment B3: the pin check, the header, the provenance and the
    evidence statuses come from one read of the current revision.

    Only the first ``current_revision`` read of the reports module returns the
    row; any later read returns ``None``.  If the report body read the revision
    again, its evidence would say ``is_current_revision: false`` while the
    header and the provenance name the current revision.
    """
    from backend.routers.v1 import reports

    _, workspace_id, meeting_id = _bundle_processed(client, email_outbox, settings, monkeypatch)
    current = current_revision_id(meeting_id)
    real_read = reports.current_revision
    revision_reads: list[str] = []

    def first_read_only(db, *, workspace_id, meeting_id):
        revision_reads.append(meeting_id)
        if len(revision_reads) > 1:
            return None
        return real_read(db, workspace_id=workspace_id, meeting_id=meeting_id)

    monkeypatch.setattr(reports, "current_revision", first_read_only)

    exported = _export(client, workspace_id, meeting_id, format="json", revision_id=current)
    assert exported.status_code == 200, exported.text
    assert revision_reads == [meeting_id]
    payload = exported.json()
    assert exported.headers["x-homayar-transcript-revision"] == current
    assert payload["provenance"]["transcript_revision"]["id"] == current
    refs = _evidence_refs(payload["report"])
    assert refs, "the grounded report cites the transcript"
    assert all(ref["is_current_revision"] for ref in refs)
    assert {ref["transcript_revision_id"] for ref in refs} == {current}

    # GET /report reads the revision once too, and agrees with the export.
    revision_reads.clear()
    report = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/report")
    assert report.status_code == 200, report.text
    assert revision_reads == [meeting_id]
    assert report.json() == payload["report"]


def test_report_export_analysis_provenance_is_the_summary_run(
    client, email_outbox, settings, monkeypatch
):
    """Amendment B3: the analysis provenance and the privacy provenance of the
    report export describe the run that wrote the summary, read once."""
    from backend.routers.v1 import insights, reports

    _, workspace_id, meeting_id = _bundle_processed(client, email_outbox, settings, monkeypatch)
    run_reads: list[str] = []
    for module in (reports, insights):
        real_read = module.current_analysis_run

        def counted(db, *, workspace_id, meeting_id, _real=real_read):
            run_reads.append(meeting_id)
            return _real(db, workspace_id=workspace_id, meeting_id=meeting_id)

        monkeypatch.setattr(module, "current_analysis_run", counted)

    exported = _export(client, workspace_id, meeting_id, format="json")
    assert exported.status_code == 200, exported.text
    payload = exported.json()
    summary_run = payload["report"]["summary"]["analysis_run_id"]
    assert summary_run is not None
    assert payload["provenance"]["analysis"]["run_id"] == summary_run
    assert payload["report"]["qa"]["quality"]["analysis_run_id"] == summary_run
    assert payload["provenance"]["privacy"]["analysis_input_view"] is not None
    # the summary names its run, so the export never asks for "the current run"
    assert run_reads == []


def test_revision_id_is_compared_case_sensitively(client, email_outbox, settings, monkeypatch):
    _, workspace_id, meeting_id = _bundle_processed(client, email_outbox, settings, monkeypatch)
    upper = current_revision_id(meeting_id).upper()
    response = _export(client, workspace_id, meeting_id, format="json", revision_id=upper)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "transcript_revision_mismatch"


def test_revision_pin_on_a_meeting_without_a_transcript(client, email_outbox, monkeypatch):
    owner, _, _ = _workspace_with_roles(client, email_outbox, monkeypatch, roles={})
    meeting_id = _insert_meeting(
        owner.workspace_id, created_by=owner.user_id, title="No transcript"
    )
    # without the param the report export answers 200 in every state
    ok = _export(client, owner.workspace_id, meeting_id, format="json")
    assert ok.status_code == 200, ok.text
    assert ok.json()["report"]["summary"] is None
    assert ok.headers["x-homayar-transcript-revision"] == "none"
    assert ok.json()["provenance"]["transcript_revision"] is None
    transcript_ok = _get(
        client, owner.workspace_id, f"/api/v1/meetings/{meeting_id}/export", format="txt"
    )
    assert transcript_ok.status_code == 200, transcript_ok.text
    assert transcript_ok.headers["x-homayar-transcript-revision"] == "none"
    # any pinned revision id is a mismatch when there is no current revision
    stale = _export(
        client, owner.workspace_id, meeting_id, format="json", revision_id=STALE_REVISION
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "transcript_revision_mismatch"
    transcript_stale = _get(
        client,
        owner.workspace_id,
        f"/api/v1/meetings/{meeting_id}/export",
        format="txt",
        revision_id=STALE_REVISION,
    )
    assert transcript_stale.status_code == 409


def _original_access_audits(meeting_id: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type == "transcript.original_accessed",
                    AuditEvent.target_id == meeting_id,
                )
            )
        )


def test_one_original_access_audit_per_request(client, email_outbox, monkeypatch, settings):
    """A1: the view is built once per request, so an original read audits once."""
    _patch_media(monkeypatch)
    owner, _, _ = _workspace_with_roles(client, email_outbox, monkeypatch, roles={})
    tenant = _owner_tenant(owner)
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    meeting_id, _ = _process(client, tenant, settings, "report-export-audit")
    base = f"/api/v1/meetings/{meeting_id}"

    report = owner.get(f"{base}/report", params={"view": "original"})
    assert report.status_code == 200, report.text
    (first,) = _original_access_audits(meeting_id)
    assert first.details["surface"] == "report"

    exported = owner.get(f"{base}/report/export", params={"format": "json", "view": "original"})
    assert exported.status_code == 200, exported.text
    audits = _original_access_audits(meeting_id)
    assert len(audits) == 2
    assert audits[-1].details["surface"] == "export"
    assert all(_no_raw(repr(audit.details)) for audit in audits)


# --- AC4: ACL deny-cases with controls ----------------------------------------


def test_unauthenticated_and_foreign_workspace_requests_are_refused(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _bundle_processed(client, email_outbox, settings, monkeypatch)
    client.cookies.clear()
    anonymous = client.get(
        f"/api/v1/meetings/{meeting_id}/report/export",
        params={"format": "json"},
        headers={"X-Workspace-ID": workspace_id},
    )
    assert anonymous.status_code == 401

    outsider = _register_and_login(client, email_outbox, "report-export-outsider@example.com")
    foreign = client.get(
        f"/api/v1/meetings/{meeting_id}/report/export",
        params={"format": "json"},
        headers={"X-Workspace-ID": outsider["workspace_id"]},
    )
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "meeting_not_found"


def _register_and_login(client, email_outbox, email: str) -> dict:
    from backend.tests.conftest import login_user, register_user

    credentials = register_user(client, email_outbox, email=email, name="Outsider")
    session = login_user(client, credentials)
    return {
        "workspace_id": session["workspaces"][0]["id"],
        "cookies": {
            name: value
            for name in ("access_token", "refresh_token", "csrf_token")
            if (value := client.cookies.get(name))
        },
    }


def test_record_scope_and_revoked_assignment_apply_with_controls(client, email_outbox, monkeypatch):
    owner, people, _ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"sup": "member", "lone": "member"}
    )
    sup = people["sup"]
    switched = owner.patch("/api/v1/admin/workspace-settings", json={"record_scope": "team"})
    assert switched.status_code == 200
    team = owner.post("/api/v1/teams", json={"name": "Team A"}).json()["id"]
    assignment = owner.post(
        f"/api/v1/teams/{team}/assignments", json={"user_id": sup.user_id, "role": "supervisor"}
    )
    assert assignment.status_code == 201, assignment.text

    mine = _insert_meeting(
        owner.workspace_id, created_by=owner.user_id, title="In scope", team_id=team
    )
    other = _insert_meeting(owner.workspace_id, created_by=owner.user_id, title="Out of scope")

    # control: the supervisor may export their team's call
    allowed = sup.get(f"/api/v1/meetings/{mine}/report/export", params={"format": "json"})
    assert allowed.status_code == 200, allowed.text
    # a call outside the record scope stays invisible
    denied = sup.get(f"/api/v1/meetings/{other}/report/export", params={"format": "json"})
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "meeting_not_found"

    # revoking the assignment takes the same call away
    revoked = owner.delete(f"/api/v1/teams/{team}/assignments/{assignment.json()['id']}")
    assert revoked.status_code == 204
    after = sup.get(f"/api/v1/meetings/{mine}/report/export", params={"format": "json"})
    assert after.status_code == 404
    # exactly the one audit of the successful control export, nothing for the 404s
    (event,) = _exported_audits(mine)
    assert event.details["artifact"] == "report"


def test_removed_member_loses_the_export(client, email_outbox, monkeypatch):
    owner, people, membership_ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    member = people["member"]
    meeting_id = _insert_meeting(owner.workspace_id, created_by=owner.user_id, title="Shared")
    before = _export(client, member.workspace_id, meeting_id, format="json")
    assert before.status_code == 200, before.text

    removed = owner.delete(f"/api/v1/members/{membership_ids['member']}")
    assert removed.status_code == 204
    after = _export(client, member.workspace_id, meeting_id, format="json")
    assert after.status_code in {403, 404}


def test_tombstoned_call_is_not_exported(client, email_outbox, monkeypatch):
    owner, _, _ = _workspace_with_roles(client, email_outbox, monkeypatch, roles={})
    meeting_id = _insert_meeting(owner.workspace_id, created_by=owner.user_id, title="Doomed")
    live = _export(client, owner.workspace_id, meeting_id, format="json")
    assert live.status_code == 200, live.text

    deleted = owner.delete(f"/api/v1/meetings/{meeting_id}")
    assert deleted.status_code == 204
    tombstoned = _export(client, owner.workspace_id, meeting_id, format="json")
    assert tombstoned.status_code == 404
    assert tombstoned.json()["error"]["code"] == "meeting_not_found"


def test_api_key_scope_gates_the_report_export(client, email_outbox, settings, monkeypatch):
    _, workspace_id, meeting_id = _bundle_processed(client, email_outbox, settings, monkeypatch)
    from backend.tests.test_authorization_matrix import _cookies, _headers

    owner_cookies = _cookies(client)
    owner_headers = _headers(owner_cookies, workspace_id)
    writer = _mint(client, owner_headers, owner_cookies, scopes=("meeting:create",))
    assert writer.status_code == 201, writer.text
    reader = _mint(client, owner_headers, owner_cookies, scopes=("meeting:read",), name="reader")
    assert reader.status_code == 201, reader.text

    client.cookies.clear()
    denied = client.get(
        f"/api/v1/meetings/{meeting_id}/report/export",
        params={"format": "json"},
        headers=_bearer(writer.json()["key"]),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "api_key_scope_denied"
    allowed = client.get(
        f"/api/v1/meetings/{meeting_id}/report/export",
        params={"format": "json"},
        headers=_bearer(reader.json()["key"]),
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.headers["x-homayar-export-version"] == "1"


# --- AC5: privacy view --------------------------------------------------------


def test_enforced_policy_redacts_every_format_and_original_needs_the_owner(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    owner, people, _ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"admin": "admin", "member": "member"}
    )
    tenant = _owner_tenant(owner)
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    meeting_id, _ = _process(client, tenant, settings, "report-export-privacy")

    for name, persona in {"owner": owner, **people}.items():
        for fmt in ("json", "csv", "pdf"):
            exported = persona.get(
                f"/api/v1/meetings/{meeting_id}/report/export", params={"format": fmt}
            )
            assert exported.status_code == 200, (name, fmt, exported.text)
            assert _no_raw(exported.content.decode("utf-8", errors="ignore")), (name, fmt)

    # view=original: owner only, audited with the export surface
    admin = people["admin"]
    denied = admin.get(
        f"/api/v1/meetings/{meeting_id}/report/export",
        params={"format": "json", "view": "original"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "original_access_denied"
    original = owner.get(
        f"/api/v1/meetings/{meeting_id}/report/export",
        params={"format": "json", "view": "original"},
    )
    assert original.status_code == 200, original.text
    assert "ali.rezaei@example.com" in original.content.decode("utf-8")
    assert original.json()["provenance"]["privacy"]["view"] == "original"

    with inspection_session_factory()() as db:
        surfaces = list(
            db.scalars(
                select(AuditEvent.details).where(
                    AuditEvent.event_type == "transcript.original_accessed",
                    AuditEvent.target_id == meeting_id,
                )
            )
        )
    assert {"export"} <= {details["surface"] for details in surfaces}
    assert all(_no_raw(repr(details)) for details in surfaces)


# --- AC7: provenance on the transcript exports --------------------------------


def test_transcript_export_carries_version_headers_and_analysis(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _bundle_processed(client, email_outbox, settings, monkeypatch)
    current = current_revision_id(meeting_id)
    for fmt in ("txt", "json", "srt", "vtt"):
        exported = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/export", format=fmt)
        assert exported.status_code == 200, exported.text
        assert exported.headers["x-homayar-export-version"] == "1", fmt
        assert exported.headers["x-homayar-transcript-revision"] == current, fmt
        assert exported.headers["cache-control"] == "no-store", fmt
    body = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/export", format="json").json()
    assert body["export_version"] == 1
    assert body["analysis"]["run_id"] is not None
    assert body["transcript_revision"]["id"] == current
    text = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/export", format="txt").text
    assert "Provenance\nExport version: 1" in text
    assert "Analysis: report_intelligence report-v1" in text


def test_share_export_carries_the_version_but_no_analysis(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _bundle_processed(client, email_outbox, settings, monkeypatch)
    enabled = client.patch(
        "/api/v1/admin/workspace-settings",
        headers=_write_headers(client, workspace_id),
        json={"public_share_links_enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    share = client.post(
        f"/api/v1/meetings/{meeting_id}/shares",
        headers=_write_headers(client, workspace_id),
        json={"expires_in_hours": 24, "require_authentication": False, "allow_export": True},
    )
    assert share.status_code == 201, share.text
    token = urlparse(share.json()["url"]).path.rsplit("/", 1)[-1]

    client.cookies.clear()
    exported = client.get(f"/api/v1/shares/{token}/export", params={"format": "json"})
    assert exported.status_code == 200, exported.text
    body = exported.json()
    assert body["export_version"] == 1
    assert body["analysis"] is None
    # a share viewer never reaches the report export either
    denied = client.get(f"/api/v1/meetings/{meeting_id}/report/export", params={"format": "json"})
    assert denied.status_code in {401, 404}


# --- B2: the published contract ----------------------------------------------


def test_contract_documents_the_transcript_export_pin():
    """Amendment B2: 1.9.0 names `revision_id`, the 409 code and the headers;
    only the export route's 409 text changes."""
    from backend.contracts import openapi_contract as contract

    snapshot = contract.load_snapshot()
    assert snapshot["info"]["version"] == "1.9.0"
    description = snapshot["info"]["description"]
    assert "**Transcript export pinning (1.9.0).**" in description
    for text in (
        "`revision_id`",
        "`409 transcript_revision_mismatch`",
        "`X-Homayar-Export-Version`",
        "`X-Homayar-Transcript-Revision`",
    ):
        assert text in description, text

    export = snapshot["paths"]["/api/v1/meetings/{meeting_id}/export"]["get"]
    assert "transcript_revision_mismatch" in export["responses"]["409"]["description"]
    assert "ingestion_conflict" not in export["responses"]["409"]["description"]
    assert "every successful export carries" in export["description"].lower()
    assert "revision_id" in {parameter["name"] for parameter in export["parameters"]}
    # control: every other route keeps the shared 409 text
    others = [
        operation["responses"]["409"]["description"]
        for path, methods in snapshot["paths"].items()
        for method, operation in methods.items()
        if "409" in operation["responses"]
        and (method, path) != ("get", "/api/v1/meetings/{meeting_id}/export")
    ]
    assert others
    assert set(others) == {contract.ERROR_DESCRIPTIONS[409]}
