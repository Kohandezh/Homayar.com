"""HOM-72 (WP14-T01): ``POST /admin/review-samples``, a recorded seeded draw.

The population is exactly the fully evaluated meetings of the window (the
review denominator of ``GET /admin/coverage``).  Every draw is recorded as one
``review_sample.drawn`` audit event whose stored details are enough to draw it
again with the same digests.  Refusals carry their own codes and record nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from backend.database.saas_models import AuditEvent, MeetingStatus, TranscriptSource
from backend.review_sampling import MAX_SEED, SampleItem, draw_sample
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _workspace_with_roles
from backend.tests.test_coverage import BASE, COVERAGE, WINDOW, Tenant, counters, seed_call

SAMPLES = "/api/v1/admin/review-samples"
EVENT = "review_sample.drawn"
DETAIL_KEYS = {
    "seed",
    "sampler_version",
    "coverage_version",
    "window_from_at",
    "window_to_at",
    "config",
    "config_digest",
    "population_count",
    "population_digest",
    "selection_digest",
    "selected_count",
}


def _events(workspace_id: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.workspace_id == workspace_id, AuditEvent.event_type == EVENT)
                .order_by(AuditEvent.occurred_at.asc())
            )
        )


def _audit_count() -> int:
    with inspection_session_factory()() as db:
        return db.scalar(select(func.count()).select_from(AuditEvent)) or 0


def _draw(tenant: Tenant, body: dict[str, Any]) -> dict[str, Any]:
    response = tenant.post(SAMPLES, {**WINDOW, **body})
    assert response.status_code == 200, response.text
    return response.json()


def test_the_population_is_exactly_the_fully_evaluated_meetings(client, email_outbox):
    tenant = Tenant(client, email_outbox, "samples-population@example.com")
    eligible = {seed_call(tenant) for _ in range(3)}
    seed_call(tenant, status=MeetingStatus.FAILED)  # transcript and analysis, still failed
    seed_call(tenant, status=MeetingStatus.CANCELLED)
    seed_call(tenant, deleted=True)
    seed_call(tenant, status=MeetingStatus.PARTIAL)
    seed_call(tenant, run="stale")
    seed_call(tenant, run_mock=True)
    seed_call(tenant, ledger="rejected")
    seed_call(tenant, received_at=BASE + timedelta(days=1))  # == to: outside the window
    other = Tenant(client, email_outbox, "samples-other@example.com")
    foreign = {seed_call(other) for _ in range(2)}

    body = _draw(tenant, {"seed": 42, "config": {"random_size": 500}})
    assert set(body) == {"coverage_version", "window", "draw"}
    assert body["coverage_version"] == "coverage/1"
    assert body["window"]["basis"] == "ingestion_received_at"
    draw = body["draw"]
    assert draw["sampler_version"] == "review-sampling/1"
    assert draw["seed"] == 42
    assert draw["population_count"] == draw["scoped_count"] == 3
    assert {s["item_id"] for s in draw["selections"]} == eligible
    assert not foreign & {s["item_id"] for s in draw["selections"]}
    assert draw["shortfalls"] == [
        {"method": "random", "stratum": None, "requested": 500, "selected": 3}
    ]
    # the population is the review denominator of the coverage counters
    assert counters(tenant)["review"]["denominator"] == draw["population_count"]

    expected = draw_sample(
        [
            SampleItem(meeting_id, strata={"language": "fa", "source": "upload"}, risk_flags=[])
            for meeting_id in eligible
        ],
        draw["config"],
        seed=42,
    ).to_json()
    assert draw == expected


def test_strata_and_risk_flags_come_from_the_meeting(client, email_outbox):
    tenant = Tenant(client, email_outbox, "samples-flags@example.com")
    team = tenant.post("/api/v1/teams", {"name": "Sampled team"})
    assert team.status_code == 201, team.text
    team_id = team.json()["id"]
    plain = seed_call(
        tenant, team_id=team_id, agent_user_id=tenant.owner_id, source="api", language="en"
    )
    partial = seed_call(tenant, summary_status="partial")
    unknown = seed_call(tenant, diarization=None)
    corrected = seed_call(tenant, revision_source=TranscriptSource.CORRECTION)

    draw = _draw(
        tenant,
        {
            "seed": 7,
            "config": {
                "risk_flags": [
                    "evidence_partial",
                    "speaker_attribution_unknown",
                    "transcript_corrected",
                ],
                "stratify_by": ["team_id", "agent_user_id"],
                "per_stratum_size": 5,
            },
        },
    )["draw"]
    by_id = {s["item_id"]: s for s in draw["selections"]}
    assert set(by_id) == {plain, partial, unknown, corrected}
    assert draw["risk_flagged_count"] == 3
    assert by_id[plain]["reasons"] == ["stratified"]
    assert by_id[plain]["stratum"] == {"agent_user_id": tenant.owner_id, "team_id": team_id}
    assert by_id[partial]["risk_flags"] == ["evidence_partial"]
    assert by_id[unknown]["risk_flags"] == ["speaker_attribution_unknown"]
    assert by_id[corrected]["risk_flags"] == ["transcript_corrected"]
    for meeting_id in (partial, unknown, corrected):
        assert by_id[meeting_id]["reasons"] == ["risk", "stratified"]
        assert by_id[meeting_id]["stratum"] == {
            "agent_user_id": "unassigned",
            "team_id": "unassigned",
        }

    scoped = _draw(
        tenant, {"seed": 7, "config": {"random_size": 10, "scope": {"source": ["api"]}}}
    )["draw"]
    assert [s["item_id"] for s in scoped["selections"]] == [plain]
    assert (scoped["population_count"], scoped["scoped_count"]) == (4, 1)


def test_the_audit_row_reproduces_the_draw(client, email_outbox):
    tenant = Tenant(client, email_outbox, "samples-audit@example.com")
    for index in range(6):
        seed_call(
            tenant,
            summary_status="partial" if index % 2 else "sufficient",
            source="api" if index % 3 else "upload",
        )
    config = {
        "random_size": 2,
        "risk_flags": ["evidence_partial"],
        "risk_cap": 1,
        "stratify_by": ["source"],
        "per_stratum_size": 1,
        "scope": {"language": ["fa"]},
    }
    first = _draw(tenant, {"config": config})  # no seed: the server picks one
    seed = first["draw"]["seed"]
    assert isinstance(seed, int) and 0 <= seed <= MAX_SEED

    [event] = _events(tenant.workspace_id)
    assert event.actor_user_id == tenant.owner_id
    assert event.target_type == "workspace" and event.target_id == tenant.workspace_id
    assert event.outcome == "success"
    details = event.details
    assert set(details) == DETAIL_KEYS
    draw = first["draw"]
    assert details == {
        "seed": seed,
        "sampler_version": "review-sampling/1",
        "coverage_version": "coverage/1",
        "window_from_at": details["window_from_at"],
        "window_to_at": details["window_to_at"],
        "config": draw["config"],
        "config_digest": draw["config_digest"],
        "population_count": draw["population_count"],
        "population_digest": draw["population_digest"],
        "selection_digest": draw["selection_digest"],
        "selected_count": len(draw["selections"]),
    }
    assert set(details["config"]) == {
        "random_size",
        "stratify_by",
        "per_stratum_size",
        "risk_flags",
        "risk_cap",
        "scope",
    }
    for key in ("window_from_at", "window_to_at"):
        assert datetime.fromisoformat(details[key]).tzinfo is not None
    assert datetime.fromisoformat(details["window_from_at"]) == datetime.fromisoformat(
        first["window"]["from"]
    )

    # a new draw built only from the stored row
    again = _draw(
        tenant,
        {
            "from": details["window_from_at"],
            "to": details["window_to_at"],
            "seed": details["seed"],
            "config": details["config"],
        },
    )["draw"]
    assert again["population_digest"] == details["population_digest"]
    assert again["selection_digest"] == details["selection_digest"]
    assert again["config_digest"] == details["config_digest"]
    assert again == draw
    assert len(_events(tenant.workspace_id)) == 2

    # a changed population shows in the digest
    seed_call(tenant)
    changed = _draw(tenant, {"seed": seed, "config": details["config"]})["draw"]
    assert changed["population_digest"] != details["population_digest"]


def test_refusals_carry_their_code_and_record_nothing(client, email_outbox):
    tenant = Tenant(client, email_outbox, "samples-refused@example.com")
    seed_call(tenant)
    before = _audit_count()
    cases = [
        ({"config": {"random_size": 501}}, "sampling_config_invalid"),
        (
            {"config": {"stratify_by": ["team_id"], "per_stratum_size": 0}},
            "sampling_config_invalid",
        ),
        ({"config": {"random_size": 1, "region": 1}}, "sampling_config_invalid"),
        ({"config": {"risk_flags": ["needs_review"]}}, "sampling_config_invalid"),
        ({"config": "random_size=1"}, "sampling_config_invalid"),
        ({"config": {}}, "sampling_config_empty"),
        ({"config": {"random_size": 1}, "seed": -1}, "seed_invalid"),
        ({"config": {"random_size": 1}, "seed": MAX_SEED + 1}, "seed_invalid"),
        ({"config": {"random_size": 1}, "seed": True}, "seed_invalid"),
        ({"config": {"random_size": 1}, "seed": 1.5}, "seed_invalid"),
        ({"config": {"random_size": 1}, "seed": "1"}, "seed_invalid"),
        ({"config": {"random_size": 1}, "from": "2026-03-01T09:00:00"}, "coverage_window_invalid"),
        (
            {
                "config": {"random_size": 1},
                "from": "2026-03-02T09:00:00Z",
                "to": "2026-03-01T09:00:00Z",
            },
            "coverage_window_invalid",
        ),
        (
            {
                "config": {"random_size": 1},
                "from": "2026-01-01T00:00:00Z",
                "to": "2027-01-03T00:00:00Z",
            },
            "coverage_window_invalid",
        ),
        ({"config": {"random_size": 1}, "extra": True}, "validation_error"),
        ({"seed": 1}, "validation_error"),
    ]
    for body, code in cases:
        refused = tenant.post(SAMPLES, body)
        assert refused.status_code == 422, (body, refused.text)
        assert refused.json()["error"]["code"] == code, body
    assert _audit_count() == before

    for body in (
        {"config": {"random_size": 500}, "seed": 0},
        {"config": {"random_size": 1}, "seed": MAX_SEED},
        {"config": {"stratify_by": ["team_id"], "per_stratum_size": 100}, "seed": 1},
        {
            "config": {"random_size": 1},
            "from": "2026-01-01T00:00:00Z",
            "to": "2027-01-02T00:00:00Z",
        },
    ):
        allowed = tenant.post(SAMPLES, body)
        assert allowed.status_code == 200, (body, allowed.text)
    assert _audit_count() == before + 4


def test_only_owner_and_admin_draw_samples(client, email_outbox, monkeypatch):
    owner, personas, _ = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"admin": "admin", "sup": "member", "agent": "member", "viewer": "viewer"},
    )
    team = owner.post("/api/v1/teams", json={"name": "Sampling team"}).json()["id"]
    for name, role in (("sup", "supervisor"), ("agent", "agent")):
        assigned = owner.post(
            f"/api/v1/teams/{team}/assignments",
            json={"user_id": personas[name].user_id, "role": role},
        )
        assert assigned.status_code == 201, assigned.text
    body = {**WINDOW, "seed": 1, "config": {"random_size": 1}}

    for persona in (owner, personas["admin"]):
        allowed = persona.post(SAMPLES, json=body)
        assert allowed.status_code == 200, allowed.text
    assert len(_events(owner.workspace_id)) == 2
    for name in ("sup", "agent", "viewer"):
        denied = personas[name].post(SAMPLES, json=body)
        assert denied.status_code == 403, (name, denied.text)
        assert denied.json()["error"]["code"] == "permission_denied"

    # a cookie session without the CSRF header is refused
    admin = personas["admin"]
    no_csrf = client.post(
        SAMPLES, json=body, cookies=admin.cookies, headers={"X-Workspace-ID": admin.workspace_id}
    )
    assert no_csrf.status_code == 403, no_csrf.text
    assert len(_events(owner.workspace_id)) == 2


def test_api_keys_cannot_draw_samples(client, email_outbox):
    tenant = Tenant(client, email_outbox, "samples-keys@example.com")
    minted = _mint(client, tenant.headers, tenant.cookies, scopes=["meeting:read", "audit:read"])
    assert minted.status_code == 201, minted.text
    headers = _bearer(minted.json()["key"], **{"X-Workspace-ID": tenant.workspace_id})
    denied = client.post(SAMPLES, json={"config": {"random_size": 1}}, headers=headers)
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "api_key_scope_denied"
    assert client.get(COVERAGE, headers=headers).status_code == 403
    assert _events(tenant.workspace_id) == []


# --- turn 2: window overflow, scope value shapes, seed null ---------------------


def test_out_of_range_dates_are_422_on_review_samples(client, email_outbox):
    from backend.tests.test_coverage import EDGE_CONTROL, OVERFLOW_WINDOWS

    tenant = Tenant(client, email_outbox, "samples-overflow@example.com")
    before = _audit_count()
    for params in OVERFLOW_WINDOWS:
        refused = tenant.post(SAMPLES, {**params, "config": {"random_size": 1}})
        assert refused.status_code == 422, (params, refused.text)
        assert refused.json()["error"]["code"] == "coverage_window_invalid", params
    assert _audit_count() == before
    allowed = tenant.post(SAMPLES, {**EDGE_CONTROL, "config": {"random_size": 1}})
    assert allowed.status_code == 200, allowed.text


SCOPE_KEYS = ("team_id", "agent_user_id", "source", "language")
FREE_TEXT = ("09123456789", "someone@example.com", "x" * 5000, "فارسی")


def test_scope_values_must_have_the_shape_of_their_column(client, email_outbox):
    tenant = Tenant(client, email_outbox, "samples-scope@example.com")
    team = tenant.post("/api/v1/teams", {"name": "Scope team"})
    assert team.status_code == 201, team.text
    team_id = team.json()["id"]
    before = _audit_count()
    refused_values = {key: list(FREE_TEXT) for key in SCOPE_KEYS}
    refused_values["team_id"] += [team_id.upper(), "t1", f"{team_id} "]
    refused_values["agent_user_id"] += [tenant.owner_id.upper(), "UNASSIGNED"]
    refused_values["source"] += ["Upload", "1upload", "a" * 31, "up-load", ""]
    refused_values["language"] += ["f", "fars", "en-", "en-123456789", "en_US"]
    for key, values in refused_values.items():
        for value in values:
            refused = tenant.post(
                SAMPLES, {**WINDOW, "config": {"random_size": 1, "scope": {key: [value]}}}
            )
            assert refused.status_code == 422, (key, value[:40], refused.text)
            assert refused.json()["error"]["code"] == "sampling_config_invalid"
            assert value not in refused.text or len(value) < 8
        # one bad value among good ones is enough to refuse
        mixed = tenant.post(
            SAMPLES,
            {**WINDOW, "config": {"random_size": 1, "scope": {key: ["unassigned", FREE_TEXT[0]]}}},
        )
        assert mixed.status_code == 422, (key, mixed.text)
    assert _audit_count() == before

    accepted = {
        "team_id": [team_id, "unassigned"],
        "agent_user_id": [tenant.owner_id, "unassigned"],
        "source": ["upload", "voicemail", "unassigned", "a" * 30, "api_v2"],
        "language": ["fa", "en-US", "unassigned", "EN", "fil", "zh-Hant"],
    }
    for key, values in accepted.items():
        allowed = tenant.post(
            SAMPLES, {**WINDOW, "config": {"random_size": 1, "scope": {key: values}}}
        )
        assert allowed.status_code == 200, (key, allowed.text)
    allowed = tenant.post(SAMPLES, {**WINDOW, "config": {"random_size": 1, "scope": accepted}})
    assert allowed.status_code == 200, allowed.text
    events = _events(tenant.workspace_id)
    assert len(events) == len(accepted) + 1
    assert events[-1].details["config"]["scope"] == {
        key: sorted(values) for key, values in sorted(accepted.items())
    }


def test_a_null_seed_is_the_same_as_no_seed(client, email_outbox):
    tenant = Tenant(client, email_outbox, "samples-null-seed@example.com")
    seed_call(tenant)
    body = _draw(tenant, {"seed": None, "config": {"random_size": 1}})
    seed = body["draw"]["seed"]
    assert isinstance(seed, int) and not isinstance(seed, bool) and 0 <= seed <= MAX_SEED
    [event] = _events(tenant.workspace_id)
    assert event.details["seed"] == seed
    again = _draw(tenant, {"seed": seed, "config": {"random_size": 1}})
    assert again["draw"] == body["draw"]
