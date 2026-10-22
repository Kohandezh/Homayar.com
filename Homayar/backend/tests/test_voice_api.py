from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.database.relational import get_session_factory
from backend.database.saas_models import Membership, User, WorkspaceRole
from backend.database.tenant_context import bind_tenant_context
from backend.routers.v1 import voice as voice_router
from backend.security.context import CSRF_COOKIE
from backend.tests.conftest import login_user, register_user
from backend.voice.admission import AdmissionPolicy, VoiceControlPlane
from backend.voice.metrics import VoiceMetrics

API = "/api/v1/voice"


@pytest.fixture
def voice_client(client):
    plane = VoiceControlPlane(
        AdmissionPolicy(global_limit=200, default_workspace_limit=2), metrics=VoiceMetrics()
    )
    client.app.include_router(voice_router.router, prefix="/api/v1")
    client.app.dependency_overrides[voice_router.get_voice_control_plane] = lambda: plane
    yield client
    client.app.dependency_overrides.pop(voice_router.get_voice_control_plane, None)


def _login(client, email_outbox, email: str) -> str:
    credentials = register_user(client, email_outbox, email=email)
    session = login_user(client, credentials)
    return session["workspaces"][0]["id"]


def _read(ws: str) -> dict[str, str]:
    return {"X-Workspace-ID": ws}


def _write(client, ws: str) -> dict[str, str]:
    return {"X-Workspace-ID": ws, "X-CSRF-Token": client.cookies.get(CSRF_COOKIE)}


def _set_role(ws: str, role: WorkspaceRole) -> None:
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=ws)
        for membership in db.execute(
            select(Membership).where(Membership.workspace_id == ws)
        ).scalars():
            membership.role = role
        db.commit()


def _make_operator(email: str) -> None:
    with get_session_factory()() as db:
        user = db.execute(select(User).where(User.email == email)).scalar_one()
        user.system_role = "operator"
        db.commit()


def _admit(client, ws, ext):
    return client.post(
        f"{API}/calls",
        json={"external_call_id": ext, "direction": "inbound", "channel": "sip", "language": "fa"},
        headers=_write(client, ws),
    )


def test_voice_call_lifecycle_capacity_and_nodes(voice_client, email_outbox):
    client = voice_client
    ws = _login(client, email_outbox, "voice-owner@example.com")

    # A workspace owner is not a platform operator: shared media nodes refuse it.
    refused = client.post(
        f"{API}/nodes", json={"node_id": "media-x", "max_concurrent": 1}, headers=_write(client, ws)
    )
    assert refused.status_code == 403
    assert client.post(f"{API}/recover", headers=_write(client, ws)).status_code == 403
    _make_operator("voice-owner@example.com")

    rejected = _admit(client, ws, "call-0")
    assert rejected.status_code == 503
    assert rejected.json()["error"]["details"]["reason"] == "no_healthy_node"

    node = client.post(
        f"{API}/nodes",
        json={"node_id": "media-1", "max_concurrent": 10},
        headers=_write(client, ws),
    )
    assert node.status_code == 200, node.text
    assert node.json()["generation"] == 1

    admitted = _admit(client, ws, "call-1")
    assert admitted.status_code == 201, admitted.text
    call = admitted.json()
    assert call["state"] == "admitted" and call["node_id"] == "media-1"
    again = _admit(client, ws, "call-1")
    assert again.status_code == 200 and again.json()["id"] == call["id"]

    answered = client.post(
        f"{API}/calls/{call['id']}/transitions",
        json={"action": "answer", "node_generation": 1},
        headers=_write(client, ws),
    )
    assert answered.status_code == 200 and answered.json()["state"] == "active"
    illegal = client.post(
        f"{API}/calls/{call['id']}/transitions",
        json={"action": "ring", "node_generation": 1},
        headers=_write(client, ws),
    )
    assert illegal.status_code == 409
    assert illegal.json()["error"]["code"] == "illegal_transition"
    stale = client.post(
        f"{API}/calls/{call['id']}/transitions",
        json={"action": "end", "node_generation": 7},
        headers=_write(client, ws),
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_generation"

    assert _admit(client, ws, "call-2").status_code == 201
    over = _admit(client, ws, "call-3")
    assert over.status_code == 503
    assert over.json()["error"]["details"]["reason"] == "capacity_workspace"

    snapshot = client.get(f"{API}/capacity", headers=_read(ws)).json()
    assert snapshot["workspace"] == {"workspace_id": ws, "held": 2, "limit": 2}
    assert snapshot["deployment"]["nodes"][0]["held"] == 2

    ended = client.post(
        f"{API}/calls/{call['id']}/transitions",
        json={"action": "end", "node_generation": 1},
        headers=_write(client, ws),
    )
    assert ended.status_code == 200 and ended.json()["state"] == "ended"
    assert client.get(f"{API}/calls/{call['id']}", headers=_read(ws)).json()["state"] == "ended"

    drained = client.post(
        f"{API}/nodes/media-1/drain", json={"draining": True}, headers=_write(client, ws)
    )
    assert drained.status_code == 200 and drained.json()["draining"] is True
    assert _admit(client, ws, "call-4").json()["error"]["details"]["reason"] == "node_draining"
    beat = client.post(
        f"{API}/nodes/media-1/heartbeat", json={"generation": 1}, headers=_write(client, ws)
    )
    assert beat.status_code == 200
    assert (
        client.post(
            f"{API}/nodes/media-1/heartbeat", json={"generation": 2}, headers=_write(client, ws)
        ).status_code
        == 409
    )
    assert client.post(f"{API}/recover", headers=_write(client, ws)).json()["lost_nodes"] == []


def test_voice_capacity_and_recover_response_shapes(voice_client, email_outbox):
    client = voice_client
    ws = _login(client, email_outbox, "voice-shape@example.com")

    # A workspace owner that is not a platform operator gets the workspace key only.
    plain = client.get(f"{API}/capacity", headers=_read(ws)).json()
    assert list(plain.keys()) == ["workspace"]
    assert plain["workspace"] == {"workspace_id": ws, "held": 0, "limit": 2}

    _make_operator("voice-shape@example.com")
    client.post(
        f"{API}/nodes",
        json={"node_id": "media-1", "max_concurrent": 10},
        headers=_write(client, ws),
    )

    snapshot = client.get(f"{API}/capacity", headers=_read(ws)).json()
    assert list(snapshot.keys()) == ["workspace", "deployment", "metrics"]
    node = snapshot["deployment"]["nodes"][0]
    assert list(node.keys()) == [
        "node_id",
        "max_concurrent",
        "held",
        "draining",
        "healthy",
        "generation",
        "heartbeat_at",
    ]
    # heartbeat_at stays the datetime.isoformat() text, not a Pydantic datetime.
    assert node["heartbeat_at"].endswith("+00:00")
    metrics = snapshot["metrics"]
    assert list(metrics.keys()) == [
        "admitted_total",
        "active_calls",
        "rejections_by_reason",
        "releases_by_reason",
        "admission_latency_ms",
    ]
    assert list(metrics["admission_latency_ms"].keys()) == [
        "count",
        "mean",
        "p50",
        "p95",
        "p99",
        "max",
    ]

    recovered = client.post(f"{API}/recover", headers=_write(client, ws))
    assert recovered.status_code == 200
    assert recovered.json() == {
        "lost_nodes": [],
        "failed_calls": {"node_lost": [], "lease_expired": []},
    }


def test_voice_capacity_and_recover_document_named_response_schemas(voice_client):
    spec = voice_client.app.openapi()
    capacity = spec["paths"]["/api/v1/voice/capacity"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert capacity == {"$ref": "#/components/schemas/CapacityResponse"}
    recover = spec["paths"]["/api/v1/voice/recover"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert recover == {"$ref": "#/components/schemas/RecoverResponse"}


def test_voice_cross_tenant_and_permission_negatives(voice_client, email_outbox):
    client = voice_client
    ws_a = _login(client, email_outbox, "voice-a@example.com")
    _make_operator("voice-a@example.com")
    client.post(
        f"{API}/nodes",
        json={"node_id": "media-a", "max_concurrent": 10},
        headers=_write(client, ws_a),
    )
    call = _admit(client, ws_a, "a-call").json()

    ws_b = _login(client, email_outbox, "voice-b@example.com")
    assert client.get(f"{API}/calls/{call['id']}", headers=_read(ws_b)).status_code == 404
    ended = client.post(
        f"{API}/calls/{call['id']}/transitions",
        json={"action": "end", "node_generation": 1},
        headers=_write(client, ws_b),
    )
    assert ended.status_code == 404
    # B cannot select A's workspace at all.
    assert client.get(f"{API}/calls/{call['id']}", headers=_read(ws_a)).status_code == 403
    # Missing CSRF on a write is refused.
    assert client.post(
        f"{API}/calls",
        json={"external_call_id": "x", "direction": "inbound", "channel": "sip"},
        headers=_read(ws_b),
    ).status_code in {401, 403}

    _set_role(ws_b, WorkspaceRole.MEMBER)
    snap = client.get(f"{API}/capacity", headers=_read(ws_b))
    assert snap.status_code == 200 and "deployment" not in snap.json()
    for path, body in (
        ("/nodes", {"node_id": "evil", "max_concurrent": 1}),
        ("/nodes/media-a/drain", {"draining": True}),
        ("/nodes/media-a/heartbeat", {"generation": 1}),
        ("/recover", None),
    ):
        response = client.post(f"{API}{path}", json=body, headers=_write(client, ws_b))
        assert response.status_code == 403, (path, response.text)
        assert response.json()["error"]["code"] == "permission_denied"

    _set_role(ws_b, WorkspaceRole.VIEWER)
    denied = _admit(client, ws_b, "viewer-call")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_denied"

    # Unauthenticated
    client.cookies.clear()
    assert client.get(f"{API}/capacity", headers=_read(ws_a)).status_code == 401
