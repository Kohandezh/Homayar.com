from __future__ import annotations

import base64
import copy
import importlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.routing import APIRoute, APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, inspect, select, table, update

from backend.config import REVEAL_SECRETS_CONTEXT, Settings, get_settings
from backend.database.saas_models import (
    ApiKey,
    AuditEvent,
    Membership,
    Subscription,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.routers.v1 import voice as voice_router
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.tests import test_coaching_tasks_api as coaching
from backend.tests import test_review_appeals_api as appeals
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, pin_negotiated_plan, register_user
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_crm_mapping_api import _profile_body
from backend.tests.test_scorecards_api import _definition
from backend.tests.test_uploads_api import _valid_metadata
from backend.voice.admission import AdmissionPolicy, VoiceControlPlane
from backend.voice.metrics import VoiceMetrics

API = "/api/v1"
CAPABILITIES = f"{API}/capabilities"
ADMIN_CAPABILITIES = f"{API}/admin/capabilities"
MODULE_KEYS = ("quality", "insights", "copilot", "voice", "voicemail", "integrations")
MODULE_NAMES = {
    "quality": "Quality",
    "insights": "Insights",
    "copilot": "Copilot",
    "voice": "Voice",
    "voicemail": "Voicemail",
    "integrations": "Integrations",
}
MODULE_FIELDS = {
    "key",
    "name",
    "description",
    "enabled",
    "reason",
    "install_enabled",
    "licensed",
    "admin_enabled",
}
REASONS = {
    "admin": "disabled_by_admin",
    "license": "not_licensed",
    "install": "disabled_for_install",
}
ROUTER_MODULES = {
    "backend.routers.v1.scorecards": "quality",
    "backend.routers.v1.review_items": "quality",
    "backend.routers.v1.calibration_batches": "quality",
    "backend.routers.v1.review_appeals": "quality",
    "backend.routers.v1.coaching_tasks": "quality",
    "backend.routers.v1.trends": "insights",
    "backend.routers.v1.voice": "voice",
    "backend.routers.v1.voicemail": "voicemail",
    "backend.routers.v1.integrations": "integrations",
    "backend.routers.v1.webhooks": "integrations",
    "backend.routers.v1.action_proposals": "integrations",
}
BASE_ROUTE_COUNTS = {
    "backend.routers.v1.scorecards": 5,
    "backend.routers.v1.review_items": 11,
    "backend.routers.v1.calibration_batches": 8,
    "backend.routers.v1.review_appeals": 10,
    "backend.routers.v1.coaching_tasks": 9,
    "backend.routers.v1.trends": 4,
    "backend.routers.v1.voice": 8,
    "backend.routers.v1.voicemail": 5,
    "backend.routers.v1.integrations": 7,
    "backend.routers.v1.webhooks": 11,
}
OPERATOR_ROUTES = {
    ("POST", "/voice/nodes"): "NodeResponse",
    ("POST", "/voice/nodes/{node_id}/heartbeat"): "NodeResponse",
    ("POST", "/voice/nodes/{node_id}/drain"): "NodeResponse",
    ("POST", "/voice/recover"): "RecoverResponse",
}
VOICE_TENANT_ROUTES = {
    ("POST", "/voice/calls"),
    ("GET", "/voice/calls/{session_id}"),
    ("POST", "/voice/calls/{session_id}/transitions"),
    ("GET", "/voice/capacity"),
}
ROUTES = {
    "quality-read": ("quality", "GET", f"{API}/scorecards", 200),
    "quality-write": ("quality", "POST", f"{API}/scorecards/inbound_sales/versions", 201),
    "insights-read": ("insights", "GET", f"{API}/trends/issues", 200),
    "insights-session-read": ("insights", "GET", f"{API}/trends/me", 200),
    "voice-read": ("voice", "GET", f"{API}/voice/capacity", 200),
    "voice-write": ("voice", "POST", f"{API}/voice/calls", 201),
    "voicemail-read": ("voicemail", "GET", f"{API}/voicemails", 200),
    "voicemail-write": ("voicemail", "POST", f"{API}/voicemails", 202),
    "integrations-read": ("integrations", "GET", f"{API}/integrations/mapping-profiles", 200),
    "integrations-write": (
        "integrations",
        "PUT",
        f"{API}/integrations/mapping-profiles/odoo",
        201,
    ),
    "webhooks-read": ("integrations", "GET", f"{API}/webhooks/endpoints", 200),
    "webhooks-write": ("integrations", "POST", f"{API}/webhooks/endpoints", 201),
}
WRITE_ROUTES = tuple(route_id for route_id in ROUTES if route_id.endswith("-write"))
READ_ROUTE_PER_MODULE = {
    "quality": "quality-read",
    "insights": "insights-read",
    "voice": "voice-read",
    "voicemail": "voicemail-read",
    "integrations": "webhooks-read",
}
KEYRING_KEY = base64.b64encode(b"capabilities-oracle-keyring-32by").decode()
ABSENT = "<absent>"


@pytest.fixture
def settings(settings):
    return settings.model_copy(
        update={"webhook_secret_keys": f"wk1:{KEYRING_KEY}", "webhook_secret_active_key_id": "wk1"}
    )


@pytest.fixture
def app_client(client, monkeypatch):
    plane = VoiceControlPlane(
        AdmissionPolicy(global_limit=200, default_workspace_limit=20), metrics=VoiceMetrics()
    )
    client.app.dependency_overrides[voice_router.get_voice_control_plane] = lambda: plane
    monkeypatch.setattr(
        "backend.voicemail.ingest.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=42.3),
    )
    dispatched: list[str] = []
    monkeypatch.setattr(
        "backend.voicemail.ingest.dispatch_job",
        lambda job_id, _settings: dispatched.append(job_id) or True,
    )
    client.dispatched = dispatched
    yield client
    client.app.dependency_overrides.pop(voice_router.get_voice_control_plane, None)


@dataclass
class _Actor:
    client: TestClient
    email: str
    user_id: str
    workspace_id: str
    cookies: dict[str, str]

    def send(self, method, path, workspace_id=None, *, csrf=True, **kwargs):
        headers = {"X-Workspace-ID": workspace_id or self.workspace_id}
        if csrf:
            headers["X-CSRF-Token"] = self.cookies[CSRF_COOKIE]
        return self.client.request(method, path, cookies=self.cookies, headers=headers, **kwargs)

    def capabilities(self, workspace_id=None):
        response = self.send("GET", CAPABILITIES, workspace_id, csrf=False)
        assert response.status_code == 200, response.text
        return response.json()

    def module(self, key, workspace_id=None):
        modules = self.capabilities(workspace_id)["modules"]
        return next(item for item in modules if item["key"] == key)

    def put(self, key, body, workspace_id=None, *, csrf=True):
        return self.send("PUT", f"{ADMIN_CAPABILITIES}/{key}", workspace_id, csrf=csrf, json=body)


def _signup(client, email_outbox, label: str) -> _Actor:
    email = f"cap-{label}@example.com"
    credentials = register_user(client, email_outbox, email=email, name=f"Cap {label}")
    session = login_user(client, credentials)
    cookies = {
        name: value
        for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
        if (value := client.cookies.get(name))
    }
    return _Actor(client, email, session["user"]["id"], session["workspaces"][0]["id"], cookies)


def _join(actor: _Actor, workspace_id: str, role: WorkspaceRole) -> None:
    with inspection_session_factory()() as db:
        db.add(Membership(workspace_id=workspace_id, user_id=actor.user_id, role=role))
        db.commit()


def _settings_json(workspace_id: str) -> dict:
    with inspection_session_factory()() as db:
        return copy.deepcopy(db.get(Workspace, workspace_id).settings_json)


def _seed_admin(workspace_id: str, modules) -> None:
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, workspace_id)
        data = copy.deepcopy(workspace.settings_json or {})
        if modules == ABSENT:
            data.pop("modules", None)
        else:
            data["modules"] = copy.deepcopy(modules)
        workspace.settings_json = data
        db.commit()


def _license(workspace_id: str, label: str, modules=ABSENT) -> None:
    terms: dict = {"seats": 50}
    if modules != ABSENT:
        terms["modules"] = copy.deepcopy(modules)
    pin_negotiated_plan(workspace_id, f"oracle_{label}", terms)


def _drop_subscriptions(workspace_id: str) -> None:
    with inspection_session_factory()() as db:
        db.execute(delete(Subscription).where(Subscription.workspace_id == workspace_id))
        db.commit()


def _break_billing(workspace_id: str) -> None:
    with inspection_session_factory()() as db:
        engine = db.get_bind()
    statement = (
        update(Subscription)
        .where(Subscription.workspace_id == workspace_id)
        .values(plan_version=9999)
    )
    with engine.connect() as connection:
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.execute(statement)
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
        else:
            connection.exec_driver_sql("SET LOCAL session_replication_role = replica")
            connection.execute(statement)
            connection.commit()


def _settings_with(settings: Settings, **changes) -> Settings:
    data = settings.model_dump(context={REVEAL_SECRETS_CONTEXT: True})
    data.update(changes)
    data["_env_file"] = None
    return Settings(**data)


def _install(client, settings: Settings, disabled: str) -> None:
    configured = _settings_with(settings, disabled_modules=disabled)
    client.app.dependency_overrides[get_settings] = lambda: configured


def _restore_install(client, settings: Settings) -> None:
    client.app.dependency_overrides[get_settings] = lambda: settings


def _make_operator(actor: _Actor) -> None:
    with inspection_session_factory()() as db:
        user = db.execute(select(User).where(User.id == actor.user_id)).scalar_one()
        user.system_role = "operator"
        db.commit()


def _register_node(actor: _Actor) -> None:
    _make_operator(actor)
    node = actor.send(
        "POST", f"{API}/voice/nodes", json={"node_id": "media-1", "max_concurrent": 10}
    )
    assert node.status_code == 200, node.text


def _prepare(route_id: str, actor: _Actor) -> None:
    if route_id == "voice-write":
        _register_node(actor)


def _call(route_id: str, actor: _Actor, workspace_id=None, *, label: str = "1"):
    _, method, path, _ = ROUTES[route_id]
    if route_id == "quality-write":
        body = {"definition": _definition(), "reason": "oracle", "expected_version": 0}
        return actor.send(method, path, workspace_id, json=body)
    if route_id == "voice-write":
        body = {
            "external_call_id": f"oracle-call-{label}",
            "direction": "inbound",
            "channel": "sip",
            "language": "fa",
        }
        return actor.send(method, path, workspace_id, json=body)
    if route_id == "voicemail-write":
        return actor.send(
            method,
            path,
            workspace_id,
            data={
                "external_message_id": f"oracle-msg-{label}",
                "caller_number": "0912 345 6789",
                "mailbox": "sales",
            },
            files={"file": ("vm.wav", b"voicemail wav bytes", "audio/wav")},
        )
    if route_id == "integrations-write":
        return actor.send(method, path, workspace_id, json=_profile_body())
    if route_id == "webhooks-write":
        body = {"url": f"https://hooks.example.com/oracle-{label}", "event_types": ["webhook.test"]}
        return actor.send(method, path, workspace_id, json=body)
    return actor.send(method, path, workspace_id, csrf=False)


def _apply_state(client, settings, workspace_id: str, module: str, state: str) -> None:
    if state == "admin":
        _seed_admin(workspace_id, {module: {"enabled": False}})
    elif state == "license":
        _license(workspace_id, f"without_{module}", [key for key in MODULE_KEYS if key != module])
    elif state == "install":
        _install(client, settings, module)


def _error(response) -> dict:
    body = response.json()
    if isinstance(body.get("error"), dict):
        return body["error"]
    if isinstance(body.get("detail"), dict):
        return body["detail"]
    return {"code": None, "body": body}


def _code(response) -> str:
    return _error(response)["code"]


def _refusal(response) -> dict:
    assert response.status_code == 403, response.text
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert set(error) == {"code", "message", "request_id", "details"}, body
    details = error["details"]
    assert isinstance(details, dict) and set(details) == {"module", "reason"}, body
    return {"code": error["code"], "message": error["message"], **details}


def _assert_refused(response, module: str, reason: str) -> None:
    found = _refusal(response)
    assert found["code"] == "module_disabled", found
    assert found["module"] == module, found
    assert found["reason"] == reason, found
    assert isinstance(found["message"], str) and module in found["message"].lower(), found


def _not_module_refused(response) -> bool:
    if response.status_code != 403:
        return True
    return _code(response) != "module_disabled"


def _table_counts() -> dict[str, int]:
    with inspection_session_factory()() as db:
        names = sorted(inspect(db.get_bind()).get_table_names())
        return {
            name: db.execute(select(func.count()).select_from(table(name))).scalar_one()
            for name in names
        }


def _audit_rows(workspace_id: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.workspace_id == workspace_id)
                .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
            )
        )


def _audit_count(workspace_id: str) -> int:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.workspace_id == workspace_id)
        )


def _module_guards(route: APIRoute) -> list[str]:
    found: list[str] = []

    def walk(dependant) -> None:
        key = getattr(dependant.call, "homayar_module", None)
        if key is not None:
            found.append(key)
        for sub in dependant.dependencies:
            walk(sub)

    for sub in route.dependant.dependencies:
        walk(sub)
    return found


def _permission_policies(route: APIRoute) -> list[tuple[str, bool, bool]]:
    found: list[tuple[str, bool, bool]] = []

    def walk(dependant) -> None:
        permission = getattr(dependant.call, "homayar_permission", None)
        if permission is not None:
            found.append(
                (
                    permission.value,
                    getattr(dependant.call, "homayar_write", False),
                    getattr(dependant.call, "homayar_platform", False),
                )
            )
            return
        for sub in dependant.dependencies:
            walk(sub)

    for sub in route.dependant.dependencies:
        walk(sub)
    return found


def _route_key(route: APIRoute, method: str) -> tuple[str, str]:
    return method, route.path.removeprefix(API)


# --- registry and the read endpoint -------------------------------------------------


def test_capabilities_lists_the_six_modules_in_registry_order(app_client, email_outbox):
    owner = _signup(app_client, email_outbox, "registry")

    body = owner.capabilities()

    assert set(body) == {"registry_version", "workspace_id", "modules"}
    assert body["registry_version"] == 1 and type(body["registry_version"]) is int
    assert body["workspace_id"] == owner.workspace_id
    assert [item["key"] for item in body["modules"]] == list(MODULE_KEYS)
    for item in body["modules"]:
        assert set(item) == MODULE_FIELDS, item
        assert item["name"] == MODULE_NAMES[item["key"]]
        assert isinstance(item["description"], str) and item["description"].strip()
        assert item["enabled"] is True
        assert item["reason"] is None
        assert item["install_enabled"] is True
        assert item["licensed"] is True
        assert item["admin_enabled"] is True


def test_capabilities_is_readable_by_every_member_role_and_an_api_key(app_client, email_outbox):
    owner = _signup(app_client, email_outbox, "readers-owner")
    outsider = _signup(app_client, email_outbox, "readers-outsider")
    personas = {}
    for role in (WorkspaceRole.ADMIN, WorkspaceRole.MEMBER, WorkspaceRole.VIEWER):
        persona = _signup(app_client, email_outbox, f"readers-{role.value}")
        _join(persona, owner.workspace_id, role)
        personas[role.value] = persona

    for persona in personas.values():
        body = persona.capabilities(owner.workspace_id)
        assert body["workspace_id"] == owner.workspace_id
        assert [item["key"] for item in body["modules"]] == list(MODULE_KEYS)

    minted = _mint(
        app_client,
        {"X-Workspace-ID": owner.workspace_id, "X-CSRF-Token": owner.cookies[CSRF_COOKIE]},
        owner.cookies,
        scopes=["meeting:read"],
    )
    assert minted.status_code == 201, minted.text
    by_key = app_client.get(CAPABILITIES, headers=_bearer(minted.json()["key"]))
    assert by_key.status_code == 200, by_key.text
    assert by_key.json()["workspace_id"] == owner.workspace_id

    foreign = outsider.send("GET", CAPABILITIES, owner.workspace_id, csrf=False)
    assert foreign.status_code == 403
    assert _code(foreign) == "workspace_access_denied"

    app_client.cookies.clear()
    anonymous = app_client.get(CAPABILITIES, headers={"X-Workspace-ID": owner.workspace_id})
    assert anonymous.status_code == 401


def test_capabilities_is_never_module_refused_when_every_module_is_off(
    app_client, settings, email_outbox
):
    owner = _signup(app_client, email_outbox, "never-refused")
    _seed_admin(owner.workspace_id, {key: {"enabled": False} for key in MODULE_KEYS})
    admin_off = owner.capabilities()
    assert [item["reason"] for item in admin_off["modules"]] == ["disabled_by_admin"] * 6
    assert all(item["enabled"] is False for item in admin_off["modules"])

    _license(owner.workspace_id, "nothing", [])
    unlicensed = owner.capabilities()
    assert [item["reason"] for item in unlicensed["modules"]] == ["not_licensed"] * 6

    _install(app_client, settings, ",".join(MODULE_KEYS))
    installed_off = owner.capabilities()
    assert [item["reason"] for item in installed_off["modules"]] == ["disabled_for_install"] * 6
    assert all(item["install_enabled"] is False for item in installed_off["modules"])


def test_capabilities_reports_each_input_and_the_first_refusing_reason(
    app_client, settings, email_outbox
):
    owner = _signup(app_client, email_outbox, "precedence")
    _install(app_client, settings, "voice,copilot")
    _license(owner.workspace_id, "precedence", ["quality", "copilot", "integrations"])
    _seed_admin(
        owner.workspace_id,
        {
            "voice": {"enabled": False},
            "voicemail": {"enabled": False},
            "quality": {"enabled": False},
            "copilot": {"enabled": False},
            "integrations": {"enabled": True},
        },
    )

    modules = {item["key"]: item for item in owner.capabilities()["modules"]}

    expected = {
        "quality": (True, True, False, "disabled_by_admin"),
        "insights": (True, False, True, "not_licensed"),
        "copilot": (False, True, False, "disabled_for_install"),
        "voice": (False, False, False, "disabled_for_install"),
        "voicemail": (True, False, False, "not_licensed"),
        "integrations": (True, True, True, None),
    }
    for key, (install, licensed, admin, reason) in expected.items():
        item = modules[key]
        assert item["install_enabled"] is install, item
        assert item["licensed"] is licensed, item
        assert item["admin_enabled"] is admin, item
        assert item["reason"] == reason, item
        assert item["enabled"] is (reason is None), item


# --- every module entry point: enabled, admin off, not licensed, install off ---------


@pytest.mark.parametrize("state", ["enabled", "admin", "license", "install"])
@pytest.mark.parametrize("route_id", list(ROUTES))
def test_module_route_follows_the_effective_state(
    app_client, settings, email_outbox, route_id, state
):
    module, _, _, success = ROUTES[route_id]
    owner = _signup(app_client, email_outbox, "route")
    _prepare(route_id, owner)
    _apply_state(app_client, settings, owner.workspace_id, module, state)

    response = _call(route_id, owner)

    if state == "enabled":
        assert response.status_code == success, response.text
    else:
        _assert_refused(response, module, REASONS[state])


@pytest.mark.parametrize("state", ["admin", "license", "install"])
def test_every_quality_router_is_refused_together(app_client, settings, email_outbox, state):
    owner = _signup(app_client, email_outbox, "quality-routers")
    paths = (
        "scorecards",
        "review-items",
        "calibration-batches",
        "my-reviews",
        "review-appeals",
        "coaching-tasks",
        "my-coaching-tasks",
    )
    for path in paths:
        allowed = owner.send("GET", f"{API}/{path}", csrf=False)
        assert allowed.status_code == 200, (path, allowed.text)

    _apply_state(app_client, settings, owner.workspace_id, "quality", state)

    for path in paths:
        _assert_refused(owner.send("GET", f"{API}/{path}", csrf=False), "quality", REASONS[state])


def test_coaching_routes_follow_the_quality_admin_switch(app_client, email_outbox, monkeypatch):
    queue = appeals.build_appeal_queue(app_client, email_outbox, monkeypatch)
    first = coaching.created(queue.sup, appeals.reviewed(queue).id, queue.plain)
    second_target = appeals.reviewed(queue)
    task_path = f"{coaching.TASKS}/{first['id']}"
    assert coaching.listed(queue.sup) == [first["id"]]
    assert coaching.listed(queue.agent, coaching.MY_TASKS) == [first["id"]]
    seen = queue.agent.get(task_path)
    assert seen.status_code == 200, seen.text

    _seed_admin(queue.workspace_id, {"quality": {"enabled": False}})
    before = coaching.counts()
    for persona, path in (
        (queue.sup, coaching.TASKS),
        (queue.agent, coaching.MY_TASKS),
        (queue.agent, task_path),
    ):
        _assert_refused(persona.get(path), "quality", "disabled_by_admin")
    tables = _table_counts()
    refused = coaching.create(queue.sup, second_target.id, queue.plain)
    _assert_refused(refused, "quality", "disabled_by_admin")
    assert _table_counts() == tables
    assert coaching.counts() == before

    _seed_admin(queue.workspace_id, {"quality": {"enabled": True}})
    again = queue.agent.get(task_path)
    assert again.status_code == 200, again.text
    assert again.json() == seen.json()
    second = coaching.created(queue.sup, second_target.id, queue.plain)
    assert set(coaching.listed(queue.sup)) == {first["id"], second["id"]}


# --- SPEC section 13: admin switch ----------------------------------------------------


def test_admin_disable_in_a_refuses_a_and_leaves_b_enabled(app_client, email_outbox):
    owner_a = _signup(app_client, email_outbox, "tenant-a")
    owner_b = _signup(app_client, email_outbox, "tenant-b")
    _join(owner_a, owner_b.workspace_id, WorkspaceRole.MEMBER)
    _seed_admin(owner_a.workspace_id, {"voicemail": {"enabled": False}})

    _assert_refused(_call("voicemail-read", owner_a), "voicemail", "disabled_by_admin")
    same_user_in_b = _call("voicemail-read", owner_a, owner_b.workspace_id)
    assert same_user_in_b.status_code == 200, same_user_in_b.text
    other_user_in_b = _call("voicemail-read", owner_b)
    assert other_user_in_b.status_code == 200, other_user_in_b.text


ADMIN_ENTRY_CASES = [
    ("absent", ABSENT, True),
    ("empty-object", {}, True),
    ("no-entry", {"voicemail": {"enabled": False}}, True),
    ("enabled-true", {"voice": {"enabled": True}}, True),
    (
        "enabled-true-audit-fields",
        {"voice": {"enabled": True, "changed_at": "2026-11-13T10:00:00Z", "changed_by": "u"}},
        True,
    ),
    ("other-case-key", {"Voice": {"enabled": False}}, True),
    ("enabled-false", {"voice": {"enabled": False}}, False),
    ("enabled-string-false", {"voice": {"enabled": "false"}}, False),
    ("enabled-string-true", {"voice": {"enabled": "true"}}, False),
    ("enabled-zero", {"voice": {"enabled": 0}}, False),
    ("enabled-one", {"voice": {"enabled": 1}}, False),
    ("enabled-null", {"voice": {"enabled": None}}, False),
    ("enabled-missing", {"voice": {}}, False),
    ("entry-false", {"voice": False}, False),
    ("entry-true", {"voice": True}, False),
    ("entry-string", {"voice": "on"}, False),
    ("entry-null", {"voice": None}, False),
    ("entry-list", {"voice": [True]}, False),
]
ADMIN_NOT_OBJECT_CASES = [
    ("modules-list", []),
    ("modules-list-of-entries", [{"voice": {"enabled": True}}]),
    ("modules-string", "voice"),
    ("modules-true", True),
    ("modules-null", None),
    ("modules-number", 1),
]


@pytest.mark.parametrize(
    ("modules", "voice_allowed"),
    [(modules, allowed) for _, modules, allowed in ADMIN_ENTRY_CASES],
    ids=[case_id for case_id, _, _ in ADMIN_ENTRY_CASES],
)
def test_admin_entry_spellings_on_a_route(app_client, email_outbox, modules, voice_allowed):
    owner = _signup(app_client, email_outbox, "admin-entry")
    _seed_admin(owner.workspace_id, modules)

    response = _call("voice-read", owner)

    if voice_allowed:
        assert response.status_code == 200, response.text
    else:
        _assert_refused(response, "voice", "disabled_by_admin")


@pytest.mark.parametrize(
    ("modules", "voice_allowed"),
    [(modules, allowed) for _, modules, allowed in ADMIN_ENTRY_CASES],
    ids=[case_id for case_id, _, _ in ADMIN_ENTRY_CASES],
)
def test_admin_entry_spellings_in_capabilities(app_client, email_outbox, modules, voice_allowed):
    owner = _signup(app_client, email_outbox, "admin-entry-view")
    _seed_admin(owner.workspace_id, modules)

    voice = owner.module("voice")

    assert voice["admin_enabled"] is voice_allowed, voice
    assert voice["enabled"] is voice_allowed, voice
    assert voice["reason"] == (None if voice_allowed else "disabled_by_admin"), voice


@pytest.mark.parametrize(
    "modules",
    [modules for _, modules in ADMIN_NOT_OBJECT_CASES],
    ids=[case_id for case_id, _ in ADMIN_NOT_OBJECT_CASES],
)
def test_admin_modules_not_an_object_refuses_every_module(app_client, email_outbox, modules):
    owner = _signup(app_client, email_outbox, "admin-not-object")
    _seed_admin(owner.workspace_id, modules)

    for module, route_id in READ_ROUTE_PER_MODULE.items():
        _assert_refused(_call(route_id, owner), module, "disabled_by_admin")
    view = owner.capabilities()["modules"]
    assert [item["admin_enabled"] for item in view] == [False] * 6
    assert [item["reason"] for item in view] == ["disabled_by_admin"] * 6


# --- SPEC section 13: license -----------------------------------------------------------


LICENSE_CASES = [
    ("absent", ABSENT, True, True),
    ("voice-only", ["voice"], True, False),
    ("voice-and-unknown", ["voice", "bogus"], True, False),
    ("every-key", list(MODULE_KEYS), True, True),
    ("duplicate", ["voice", "voice"], True, False),
    ("empty-list", [], False, False),
    ("other-case", ["Voice"], False, False),
    ("padded", [" voice"], False, False),
    ("null", None, False, False),
    ("string", "voice", False, False),
    ("object", {"voice": True}, False, False),
    ("list-with-number", ["voice", 1], False, False),
    ("list-with-null", ["voice", None], False, False),
    ("nested-list", [["voice"]], False, False),
    ("bool", True, False, False),
]


@pytest.mark.parametrize(
    ("modules", "voice_licensed", "quality_licensed"),
    [(modules, voice, quality) for _, modules, voice, quality in LICENSE_CASES],
    ids=[case_id for case_id, _, _, _ in LICENSE_CASES],
)
def test_license_spellings_on_routes(
    app_client, email_outbox, modules, voice_licensed, quality_licensed
):
    owner = _signup(app_client, email_outbox, "license")
    _license(owner.workspace_id, "case", modules)

    voice = _call("voice-read", owner)
    quality = _call("quality-read", owner)

    if voice_licensed:
        assert voice.status_code == 200, voice.text
    else:
        _assert_refused(voice, "voice", "not_licensed")
    if quality_licensed:
        assert quality.status_code == 200, quality.text
    else:
        _assert_refused(quality, "quality", "not_licensed")


@pytest.mark.parametrize(
    ("modules", "voice_licensed", "quality_licensed"),
    [(modules, voice, quality) for _, modules, voice, quality in LICENSE_CASES],
    ids=[case_id for case_id, _, _, _ in LICENSE_CASES],
)
def test_license_spellings_in_capabilities(
    app_client, email_outbox, modules, voice_licensed, quality_licensed
):
    owner = _signup(app_client, email_outbox, "license-view")
    _license(owner.workspace_id, "case", modules)

    view = {item["key"]: item for item in owner.capabilities()["modules"]}

    assert view["voice"]["licensed"] is voice_licensed, view["voice"]
    assert view["quality"]["licensed"] is quality_licensed, view["quality"]
    assert view["voice"]["reason"] == (None if voice_licensed else "not_licensed")
    assert view["quality"]["reason"] == (None if quality_licensed else "not_licensed")


def test_license_null_refuses_every_module_and_absent_key_allows_all(app_client, email_outbox):
    owner = _signup(app_client, email_outbox, "license-null")
    for module, route_id in READ_ROUTE_PER_MODULE.items():
        response = _call(route_id, owner)
        assert response.status_code == 200, (module, response.text)

    _license(owner.workspace_id, "null", None)

    for module, route_id in READ_ROUTE_PER_MODULE.items():
        _assert_refused(_call(route_id, owner), module, "not_licensed")


def test_license_voice_only_refuses_quality_and_allows_voice(app_client, email_outbox):
    owner = _signup(app_client, email_outbox, "license-voice")
    _license(owner.workspace_id, "voice", ["voice"])

    _assert_refused(_call("quality-read", owner), "quality", "not_licensed")
    voice = _call("voice-read", owner)
    assert voice.status_code == 200, voice.text


def test_license_is_per_workspace(app_client, email_outbox):
    owner_a = _signup(app_client, email_outbox, "license-a")
    owner_b = _signup(app_client, email_outbox, "license-b")
    _join(owner_a, owner_b.workspace_id, WorkspaceRole.ADMIN)
    _license(owner_a.workspace_id, "quality_only", ["quality"])

    _assert_refused(_call("voice-read", owner_a), "voice", "not_licensed")
    assert _call("voice-read", owner_a, owner_b.workspace_id).status_code == 200
    assert _call("voice-read", owner_b).status_code == 200
    assert owner_b.module("voice")["licensed"] is True


def test_no_current_subscription_licenses_every_module(app_client, email_outbox):
    owner = _signup(app_client, email_outbox, "no-subscription")
    _drop_subscriptions(owner.workspace_id)

    for module, route_id in READ_ROUTE_PER_MODULE.items():
        response = _call(route_id, owner)
        assert response.status_code == 200, (module, response.text)
    assert [item["licensed"] for item in owner.capabilities()["modules"]] == [True] * 6


def test_billing_configuration_error_fails_closed(app_client, email_outbox):
    owner = _signup(app_client, email_outbox, "billing-broken")
    _break_billing(owner.workspace_id)

    _assert_refused(_call("voice-read", owner), "voice", "not_licensed")
    _assert_refused(_call("voicemail-read", owner), "voicemail", "not_licensed")
    view = owner.capabilities()["modules"]
    assert [item["licensed"] for item in view] == [False] * 6
    assert [item["reason"] for item in view] == ["not_licensed"] * 6


ENTITLEMENTS_NOT_AN_OBJECT = [
    ("null", None),
    ("empty-list", []),
    ("list-of-every-key", list(MODULE_KEYS)),
    ("list-of-objects", [{"modules": list(MODULE_KEYS)}]),
]


@pytest.mark.parametrize(
    ("case_id", "entitlements"),
    ENTITLEMENTS_NOT_AN_OBJECT,
    ids=[case_id for case_id, _ in ENTITLEMENTS_NOT_AN_OBJECT],
)
def test_entitlements_that_are_not_an_object_license_nothing(
    app_client, email_outbox, case_id, entitlements
):
    owner = _signup(app_client, email_outbox, "entitlements-shape")
    plan_key = f"oracle_shape_{case_id.replace('-', '_')}"
    pin_negotiated_plan(owner.workspace_id, plan_key, copy.deepcopy(entitlements))

    for module, route_id in READ_ROUTE_PER_MODULE.items():
        _assert_refused(_call(route_id, owner), module, "not_licensed")
    view = owner.capabilities()["modules"]
    assert [item["licensed"] for item in view] == [False] * 6, view
    assert [item["reason"] for item in view] == ["not_licensed"] * 6, view
    assert [item["enabled"] for item in view] == [False] * 6, view


ENTITLEMENTS_OBJECT_WITHOUT_MODULES = [
    ("empty-object", {}),
    ("object-without-modules", {"seats": 50}),
    ("object-with-other-list", {"seats": 50, "features": ["voice"]}),
]


@pytest.mark.parametrize(
    ("case_id", "entitlements"),
    ENTITLEMENTS_OBJECT_WITHOUT_MODULES,
    ids=[case_id for case_id, _ in ENTITLEMENTS_OBJECT_WITHOUT_MODULES],
)
def test_entitlements_object_without_modules_licenses_every_module(
    app_client, email_outbox, case_id, entitlements
):
    owner = _signup(app_client, email_outbox, "entitlements-object")
    plan_key = f"oracle_object_{case_id.replace('-', '_')}"
    pin_negotiated_plan(owner.workspace_id, plan_key, copy.deepcopy(entitlements))

    for module, route_id in READ_ROUTE_PER_MODULE.items():
        response = _call(route_id, owner)
        assert response.status_code == 200, (module, response.text)


# --- SPEC section 13: install switch ------------------------------------------------


def test_install_voice_refuses_voice_in_every_workspace_and_allows_voicemail(
    app_client, settings, email_outbox
):
    owner_a = _signup(app_client, email_outbox, "install-a")
    owner_b = _signup(app_client, email_outbox, "install-b")
    _install(app_client, settings, "voice")

    for owner in (owner_a, owner_b):
        _assert_refused(_call("voice-read", owner), "voice", "disabled_for_install")
        voicemail = _call("voicemail-read", owner)
        assert voicemail.status_code == 200, voicemail.text
        view = owner.module("voice")
        assert view["install_enabled"] is False and view["reason"] == "disabled_for_install"


def test_install_switch_wins_over_license_and_admin(app_client, settings, email_outbox):
    owner = _signup(app_client, email_outbox, "install-first")
    _license(owner.workspace_id, "no_voice", ["quality"])
    _seed_admin(owner.workspace_id, {"voice": {"enabled": False}})
    _assert_refused(_call("voice-read", owner), "voice", "not_licensed")

    _install(app_client, settings, "voice")

    _assert_refused(_call("voice-read", owner), "voice", "disabled_for_install")


DISABLED_MODULES_PARSING = [
    ("empty", "", ()),
    ("only-commas", ",,", ()),
    ("one", "voice", ("voice",)),
    ("trimmed-and-empty-items", " voice , , quality ,", ("quality", "voice")),
    ("tab-and-newline", "\tvoicemail\n", ("voicemail",)),
    ("prefix-is-not-a-match", "voicemail", ("voicemail",)),
    ("repeated", "voice,voice", ("voice",)),
    ("all", ",".join(MODULE_KEYS), MODULE_KEYS),
]


@pytest.mark.parametrize(
    ("raw", "disabled"),
    [(raw, disabled) for _, raw, disabled in DISABLED_MODULES_PARSING],
    ids=[case_id for case_id, _, _ in DISABLED_MODULES_PARSING],
)
def test_disabled_modules_parsing_seen_through_capabilities(
    app_client, settings, email_outbox, raw, disabled
):
    owner = _signup(app_client, email_outbox, "install-parse")
    _install(app_client, settings, raw)

    view = {item["key"]: item for item in owner.capabilities()["modules"]}

    for key in MODULE_KEYS:
        off = key in disabled
        assert view[key]["install_enabled"] is not off, view[key]
        assert view[key]["reason"] == ("disabled_for_install" if off else None), view[key]


@pytest.mark.parametrize(
    "raw",
    [
        "vioce",
        "Voice",
        "VOICE",
        "voice,unknown",
        "quality;voice",
        "voice-mail",
        "insight , copilot",
    ],
)
def test_unknown_key_in_disabled_modules_fails_settings_validation(settings, raw):
    with pytest.raises(ValueError):
        _settings_with(settings, disabled_modules=raw)


@pytest.mark.parametrize("raw", ["", ",", "voice", " voice , , quality ,", ",".join(MODULE_KEYS)])
def test_known_keys_in_disabled_modules_build_settings(settings, raw):
    assert isinstance(_settings_with(settings, disabled_modules=raw), Settings)


def test_disabled_modules_is_read_from_the_environment(
    app_client, settings, email_outbox, monkeypatch
):
    owner = _signup(app_client, email_outbox, "install-env")
    data = settings.model_dump(context={REVEAL_SECRETS_CONTEXT: True})
    data.pop("disabled_modules", None)
    monkeypatch.setenv("DISABLED_MODULES", " voicemail , ")
    configured = Settings(**data, _env_file=None)
    app_client.app.dependency_overrides[get_settings] = lambda: configured

    _assert_refused(_call("voicemail-read", owner), "voicemail", "disabled_for_install")
    assert _call("voice-read", owner).status_code == 200

    monkeypatch.setenv("DISABLED_MODULES", "voicemial")
    with pytest.raises(ValueError):
        Settings(**data, _env_file=None)


# --- SPEC section 13: the admin write endpoint ------------------------------------------


def test_put_member_and_viewer_denied_admin_and_owner_allowed(app_client, email_outbox):
    owner = _signup(app_client, email_outbox, "put-owner")
    personas = {}
    for role in (WorkspaceRole.ADMIN, WorkspaceRole.MEMBER, WorkspaceRole.VIEWER):
        persona = _signup(app_client, email_outbox, f"put-{role.value}")
        _join(persona, owner.workspace_id, role)
        personas[role.value] = persona
    before = _settings_json(owner.workspace_id)
    audits = _audit_count(owner.workspace_id)

    for role in ("member", "viewer"):
        denied = personas[role].put("voice", {"enabled": False}, owner.workspace_id)
        assert denied.status_code == 403, denied.text
        assert _code(denied) == "permission_denied"
    assert _settings_json(owner.workspace_id) == before
    assert _audit_count(owner.workspace_id) == audits
    assert owner.module("voice")["admin_enabled"] is True

    by_admin = personas["admin"].put("voice", {"enabled": False}, owner.workspace_id)
    assert by_admin.status_code == 200, by_admin.text
    assert by_admin.json()["admin_enabled"] is False
    by_owner = owner.put("voice", {"enabled": True})
    assert by_owner.status_code == 200, by_owner.text
    assert by_owner.json()["admin_enabled"] is True


def test_put_refuses_an_api_key_a_missing_csrf_and_no_session(app_client, email_outbox):
    owner = _signup(app_client, email_outbox, "put-credentials")
    minted = _mint(
        app_client,
        {"X-Workspace-ID": owner.workspace_id, "X-CSRF-Token": owner.cookies[CSRF_COOKIE]},
        owner.cookies,
        scopes=["meeting:read", "meeting:create", "meeting:update"],
    )
    assert minted.status_code == 201, minted.text
    before = _settings_json(owner.workspace_id)
    audits = _audit_count(owner.workspace_id)

    by_key = app_client.put(
        f"{ADMIN_CAPABILITIES}/voice",
        json={"enabled": False},
        headers=_bearer(minted.json()["key"]),
    )
    assert by_key.status_code == 403, by_key.text
    assert _code(by_key) == "api_key_scope_denied"

    no_csrf = owner.put("voice", {"enabled": False}, csrf=False)
    assert no_csrf.status_code == 403, no_csrf.text
    assert _code(no_csrf) == "csrf_failed"

    assert _settings_json(owner.workspace_id) == before
    assert _audit_count(owner.workspace_id) == audits

    with_csrf = owner.put("voice", {"enabled": False})
    assert with_csrf.status_code == 200, with_csrf.text

    app_client.cookies.clear()
    anonymous = app_client.put(
        f"{ADMIN_CAPABILITIES}/voice",
        json={"enabled": True},
        headers={"X-Workspace-ID": owner.workspace_id},
    )
    assert anonymous.status_code == 401


PUT_BAD_BODIES = [
    ("string-true", {"enabled": "true"}),
    ("string-false", {"enabled": "false"}),
    ("one", {"enabled": 1}),
    ("zero", {"enabled": 0}),
    ("null", {"enabled": None}),
    ("missing", {}),
    ("other-case-field", {"Enabled": True}),
    ("extra-field", {"enabled": True, "module": "voice"}),
    ("list-body", [True]),
    ("bare-bool", True),
]


@pytest.mark.parametrize(
    "body", [body for _, body in PUT_BAD_BODIES], ids=[case_id for case_id, _ in PUT_BAD_BODIES]
)
def test_put_body_is_strict(app_client, email_outbox, body):
    owner = _signup(app_client, email_outbox, "put-body")
    before = _settings_json(owner.workspace_id)
    audits = _audit_count(owner.workspace_id)

    refused = owner.put("voice", body)

    assert refused.status_code == 422, refused.text
    assert _code(refused) == "validation_error"
    assert _settings_json(owner.workspace_id) == before
    assert _audit_count(owner.workspace_id) == audits
    allowed = owner.put("voice", {"enabled": True})
    assert allowed.status_code == 200, allowed.text


def test_put_without_a_body_is_422(app_client, email_outbox):
    owner = _signup(app_client, email_outbox, "put-no-body")
    refused = owner.send("PUT", f"{ADMIN_CAPABILITIES}/voice")
    assert refused.status_code == 422, refused.text


@pytest.mark.parametrize("key", ["Voice", "VOICE", "%20voice", "voice%20", "nope", "voicemails"])
def test_put_unknown_module_key_is_404(app_client, email_outbox, key):
    owner = _signup(app_client, email_outbox, "put-unknown")
    before = _settings_json(owner.workspace_id)
    audits = _audit_count(owner.workspace_id)

    missing = owner.put(key, {"enabled": False})

    assert missing.status_code == 404, missing.text
    assert _code(missing) == "module_not_found"
    assert _settings_json(owner.workspace_id) == before
    assert _audit_count(owner.workspace_id) == audits


@pytest.mark.parametrize("key", list(MODULE_KEYS))
def test_put_every_known_module_key_is_200(app_client, email_outbox, key):
    owner = _signup(app_client, email_outbox, "put-known")

    response = owner.put(key, {"enabled": False})

    assert response.status_code == 200, response.text
    assert response.json()["key"] == key


def test_put_returns_the_module_audits_every_write_and_keeps_other_settings(
    app_client, email_outbox
):
    owner = _signup(app_client, email_outbox, "put-audit")
    _seed_admin(owner.workspace_id, {"insights": {"enabled": False}})
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, owner.workspace_id)
        data = copy.deepcopy(workspace.settings_json)
        data["oracle_marker"] = {"nested": [1, "two", {"three": True}]}
        workspace.settings_json = data
        db.commit()
    before = _settings_json(owner.workspace_id)
    seen = {event.id for event in _audit_rows(owner.workspace_id)}

    disabled = owner.put("voice", {"enabled": False})
    assert disabled.status_code == 200, disabled.text
    assert set(disabled.json()) == MODULE_FIELDS
    assert disabled.json()["key"] == "voice"
    assert disabled.json()["name"] == "Voice"
    assert disabled.json()["enabled"] is False
    assert disabled.json()["admin_enabled"] is False
    assert disabled.json()["install_enabled"] is True
    assert disabled.json()["licensed"] is True
    assert disabled.json()["reason"] == "disabled_by_admin"
    assert owner.module("voice") == disabled.json()
    _assert_refused(_call("voice-read", owner), "voice", "disabled_by_admin")

    again = owner.put("voice", {"enabled": False})
    assert again.status_code == 200, again.text
    assert again.json() == disabled.json()

    enabled = owner.put("voice", {"enabled": True})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] is True and enabled.json()["reason"] is None
    assert owner.module("voice") == enabled.json()
    assert _call("voice-read", owner).status_code == 200

    after = _settings_json(owner.workspace_id)
    assert {k: v for k, v in after.items() if k != "modules"} == {
        k: v for k, v in before.items() if k != "modules"
    }
    assert after["modules"]["insights"] == {"enabled": False}
    assert after["modules"]["voice"]["enabled"] is True
    assert owner.module("insights")["admin_enabled"] is False

    new_events = [event for event in _audit_rows(owner.workspace_id) if event.id not in seen]
    assert len(new_events) == 3, [(event.event_type, event.details) for event in new_events]
    for event in new_events:
        assert event.target_type == "workspace"
        assert event.target_id == owner.workspace_id
        assert event.actor_user_id == owner.user_id
        assert event.outcome == "success"
    first, second, third = new_events
    assert (first.event_type, first.details) == (second.event_type, second.details)
    assert (first.event_type, first.details) != (third.event_type, third.details)


def test_put_changes_only_the_callers_workspace(app_client, email_outbox):
    owner_a = _signup(app_client, email_outbox, "put-a")
    owner_b = _signup(app_client, email_outbox, "put-b")
    _join(owner_a, owner_b.workspace_id, WorkspaceRole.ADMIN)
    before_b = _settings_json(owner_b.workspace_id)
    audits_b = _audit_count(owner_b.workspace_id)

    response = owner_a.put("voicemail", {"enabled": False})
    assert response.status_code == 200, response.text

    assert _settings_json(owner_b.workspace_id) == before_b
    assert _audit_count(owner_b.workspace_id) == audits_b
    _assert_refused(_call("voicemail-read", owner_a), "voicemail", "disabled_by_admin")
    assert _call("voicemail-read", owner_a, owner_b.workspace_id).status_code == 200
    assert _call("voicemail-read", owner_b).status_code == 200
    assert owner_b.module("voicemail")["enabled"] is True
    assert owner_a.module("voicemail", owner_b.workspace_id)["enabled"] is True


def test_put_is_kept_whatever_the_license_or_install_state(app_client, settings, email_outbox):
    owner = _signup(app_client, email_outbox, "put-kept")
    _install(app_client, settings, "voice")

    off = owner.put("voice", {"enabled": False})
    assert off.status_code == 200, off.text
    assert off.json()["install_enabled"] is False
    assert off.json()["admin_enabled"] is False
    assert off.json()["reason"] == "disabled_for_install"
    on = owner.put("voice", {"enabled": True})
    assert on.status_code == 200, on.text
    assert on.json()["admin_enabled"] is True
    assert on.json()["enabled"] is False
    assert on.json()["reason"] == "disabled_for_install"

    _restore_install(app_client, settings)
    _license(owner.workspace_id, "quality_only", ["quality"])
    unlicensed_off = owner.put("voice", {"enabled": False})
    assert unlicensed_off.status_code == 200, unlicensed_off.text
    assert unlicensed_off.json()["licensed"] is False
    assert unlicensed_off.json()["reason"] == "not_licensed"

    _license(owner.workspace_id, "all_modules")
    view = owner.module("voice")
    assert view["licensed"] is True and view["admin_enabled"] is False
    assert view["reason"] == "disabled_by_admin"
    _assert_refused(_call("voice-read", owner), "voice", "disabled_by_admin")


# --- SPEC section 13: refused writes create nothing; off then on keeps data ------------


@pytest.mark.parametrize("state", ["admin", "license", "install"])
@pytest.mark.parametrize("route_id", WRITE_ROUTES)
def test_refused_write_creates_nothing_and_the_same_write_enabled_succeeds(
    app_client, settings, email_outbox, route_id, state
):
    module, _, _, success = ROUTES[route_id]
    owner = _signup(app_client, email_outbox, "refused-write")
    _prepare(route_id, owner)
    _apply_state(app_client, settings, owner.workspace_id, module, state)
    before = _table_counts()

    refused = _call(route_id, owner)

    _assert_refused(refused, module, REASONS[state])
    assert _table_counts() == before
    assert app_client.dispatched == []

    _seed_admin(owner.workspace_id, ABSENT)
    _license(owner.workspace_id, "everything")
    _restore_install(app_client, settings)
    allowed = _call(route_id, owner)
    assert allowed.status_code == success, allowed.text
    assert _table_counts() != before


def test_off_then_on_keeps_data_in_both_tenants(app_client, email_outbox):
    owner_a = _signup(app_client, email_outbox, "keep-a")
    owner_b = _signup(app_client, email_outbox, "keep-b")
    hook_a = _call("webhooks-write", owner_a, label="a")
    assert hook_a.status_code == 201, hook_a.text
    hook_b = _call("webhooks-write", owner_b, label="b")
    assert hook_b.status_code == 201, hook_b.text
    scorecard = _call("quality-write", owner_a)
    assert scorecard.status_code == 201, scorecard.text
    endpoint_a = f"{API}/webhooks/endpoints/{hook_a.json()['endpoint']['id']}"
    endpoint_b = f"{API}/webhooks/endpoints/{hook_b.json()['endpoint']['id']}"
    versions = f"{API}/scorecards/inbound_sales/versions"
    snapshot_a = owner_a.send("GET", endpoint_a, csrf=False).json()
    snapshot_b = owner_b.send("GET", endpoint_b, csrf=False).json()
    snapshot_versions = owner_a.send("GET", versions, csrf=False).json()
    rows_before = _table_counts()

    for key in ("integrations", "quality"):
        off = owner_a.put(key, {"enabled": False})
        assert off.status_code == 200, off.text

    _assert_refused(
        owner_a.send("GET", endpoint_a, csrf=False), "integrations", "disabled_by_admin"
    )
    _assert_refused(owner_a.send("GET", versions, csrf=False), "quality", "disabled_by_admin")
    assert owner_b.send("GET", endpoint_b, csrf=False).json() == snapshot_b
    rows_off = _table_counts()
    assert {k: v for k, v in rows_off.items() if k != "audit_events"} == {
        k: v for k, v in rows_before.items() if k != "audit_events"
    }

    for key in ("integrations", "quality"):
        on = owner_a.put(key, {"enabled": True})
        assert on.status_code == 200, on.text

    assert owner_a.send("GET", endpoint_a, csrf=False).json() == snapshot_a
    assert owner_a.send("GET", versions, csrf=False).json() == snapshot_versions
    assert owner_b.send("GET", endpoint_b, csrf=False).json() == snapshot_b


# --- SPEC section 13: voice operator routes, refusal order ------------------------------


@pytest.mark.parametrize("state", ["admin", "license", "install"])
def test_voice_calls_refused_but_operator_routes_not_module_refused(
    app_client, settings, email_outbox, state
):
    owner = _signup(app_client, email_outbox, "voice-operator")
    _make_operator(owner)
    _apply_state(app_client, settings, owner.workspace_id, "voice", state)

    _assert_refused(_call("voice-write", owner), "voice", REASONS[state])
    node = owner.send(
        "POST", f"{API}/voice/nodes", json={"node_id": "media-1", "max_concurrent": 10}
    )
    assert node.status_code == 200, node.text
    beat = owner.send(
        "POST",
        f"{API}/voice/nodes/media-1/heartbeat",
        json={"generation": node.json()["generation"]},
    )
    assert beat.status_code == 200, beat.text
    drain = owner.send("POST", f"{API}/voice/nodes/media-1/drain", json={"draining": True})
    assert drain.status_code == 200, drain.text
    recover = owner.send("POST", f"{API}/voice/recover")
    assert recover.status_code == 200, recover.text


def test_voice_operator_routes_keep_their_refusal_for_non_operators(
    app_client, settings, email_outbox
):
    owner = _signup(app_client, email_outbox, "voice-not-operator")
    _install(app_client, settings, "voice")

    node = owner.send(
        "POST", f"{API}/voice/nodes", json={"node_id": "media-1", "max_concurrent": 10}
    )
    assert node.status_code == 403, node.text
    assert _not_module_refused(node)
    recover = owner.send("POST", f"{API}/voice/recover")
    assert recover.status_code == 403, recover.text
    assert _not_module_refused(recover)


@pytest.mark.parametrize("state", ["admin", "install"])
def test_refusal_order_is_auth_then_membership_then_module(
    app_client, settings, email_outbox, state
):
    owner = _signup(app_client, email_outbox, "order-owner")
    outsider = _signup(app_client, email_outbox, "order-outsider")
    _apply_state(app_client, settings, owner.workspace_id, "voicemail", state)

    foreign = _call("voicemail-read", outsider, owner.workspace_id)
    assert foreign.status_code == 403, foreign.text
    assert _code(foreign) == "workspace_access_denied"

    _assert_refused(_call("voicemail-read", owner), "voicemail", REASONS[state])

    app_client.cookies.clear()
    anonymous = app_client.get(f"{API}/voicemails", headers={"X-Workspace-ID": owner.workspace_id})
    assert anonymous.status_code == 401, anonymous.text


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, WorkspaceRole.MEMBER])
def test_module_refusal_does_not_depend_on_the_role(app_client, email_outbox, role):
    owner = _signup(app_client, email_outbox, "role-owner")
    persona = _signup(app_client, email_outbox, f"role-{role.value}")
    _join(persona, owner.workspace_id, role)

    enabled = _call("webhooks-write", persona, owner.workspace_id)
    assert enabled.status_code == 403, enabled.text
    assert _code(enabled) == "permission_denied"

    _seed_admin(owner.workspace_id, {"integrations": {"enabled": False}})

    _assert_refused(
        _call("webhooks-write", persona, owner.workspace_id), "integrations", "disabled_by_admin"
    )
    _assert_refused(_call("webhooks-write", owner), "integrations", "disabled_by_admin")
    _assert_refused(
        _call("webhooks-read", persona, owner.workspace_id), "integrations", "disabled_by_admin"
    )


def test_api_key_is_module_refused_on_a_disabled_module(app_client, email_outbox):
    owner = _signup(app_client, email_outbox, "key-module")
    minted = _mint(
        app_client,
        {"X-Workspace-ID": owner.workspace_id, "X-CSRF-Token": owner.cookies[CSRF_COOKIE]},
        owner.cookies,
        scopes=["meeting:read"],
    )
    assert minted.status_code == 201, minted.text
    key_headers = _bearer(minted.json()["key"])

    enabled = app_client.get(f"{API}/voicemails", headers=key_headers)
    assert enabled.status_code == 200, enabled.text

    _seed_admin(owner.workspace_id, {"voicemail": {"enabled": False}})
    _assert_refused(
        app_client.get(f"{API}/voicemails", headers=key_headers), "voicemail", "disabled_by_admin"
    )
    assert app_client.get(CAPABILITIES, headers=key_headers).status_code == 200

    _seed_admin(owner.workspace_id, ABSENT)
    _license(owner.workspace_id, "voice_only", ["voice"])
    _assert_refused(
        app_client.get(f"{API}/voicemails", headers=key_headers), "voicemail", "not_licensed"
    )


SESSION_ONLY_TRENDS = (f"{API}/trends/teams", f"{API}/trends/me", f"{API}/trends/conversations")
KEY_STATES = ["enabled", "admin", "license", "install"]


def _mint_key(client, owner: _Actor, name: str) -> dict:
    minted = _mint(
        client,
        {"X-Workspace-ID": owner.workspace_id, "X-CSRF-Token": owner.cookies[CSRF_COOKIE]},
        owner.cookies,
        name=name,
        scopes=["meeting:read"],
    )
    assert minted.status_code == 201, minted.text
    return minted.json()


def _assert_key_not_accepted(response, label) -> None:
    assert response.status_code == 401, (label, response.text)
    assert _code(response) == "api_key_not_accepted", (label, response.text)


@pytest.mark.parametrize("state", ["admin", "license", "install"])
def test_api_key_on_session_only_trends_is_not_accepted_while_refused(
    app_client, settings, email_outbox, state
):
    owner = _signup(app_client, email_outbox, "key-trends")
    key_headers = _bearer(_mint_key(app_client, owner, "key-trends")["key"])
    app_client.cookies.clear()

    for path in SESSION_ONLY_TRENDS:
        _assert_key_not_accepted(app_client.get(path, headers=key_headers), ("enabled", path))
    key_route = app_client.get(f"{API}/trends/issues", headers=key_headers)
    assert key_route.status_code == 200, key_route.text

    _apply_state(app_client, settings, owner.workspace_id, "insights", state)

    for path in SESSION_ONLY_TRENDS:
        _assert_key_not_accepted(app_client.get(path, headers=key_headers), (state, path))
    _assert_refused(
        app_client.get(f"{API}/trends/issues", headers=key_headers), "insights", REASONS[state]
    )
    _assert_refused(owner.send("GET", f"{API}/trends/me", csrf=False), "insights", REASONS[state])


@pytest.mark.parametrize("state", KEY_STATES)
def test_every_api_key_bearer_on_session_only_trends_is_not_accepted_in_every_state(
    app_client, settings, email_outbox, state
):
    owner = _signup(app_client, email_outbox, "key-bearers")
    other = _signup(app_client, email_outbox, "key-bearers-other")
    valid = _mint_key(app_client, owner, "valid")["key"]
    revoked = _mint_key(app_client, owner, "revoked")
    gone = owner.send("DELETE", f"{API}/api-keys/{revoked['id']}")
    assert gone.status_code == 204, gone.text
    expired = _mint_key(app_client, owner, "expired")
    with inspection_session_factory()() as db:
        db.get(ApiKey, expired["id"]).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    bearers = {
        "valid": _bearer(valid),
        "valid, own workspace header": _bearer(valid, **{"X-Workspace-ID": owner.workspace_id}),
        "valid, foreign workspace header": _bearer(valid, **{"X-Workspace-ID": other.workspace_id}),
        "unknown": _bearer("hk_00000000.nope"),
        "revoked": _bearer(revoked["key"]),
        "expired": _bearer(expired["key"]),
    }
    _apply_state(app_client, settings, owner.workspace_id, "insights", state)
    app_client.cookies.clear()

    for label, headers in bearers.items():
        for path in SESSION_ONLY_TRENDS:
            _assert_key_not_accepted(app_client.get(path, headers=headers), (state, label, path))

    issues = f"{API}/trends/issues"
    allowed = app_client.get(issues, headers=bearers["valid"])
    if state == "enabled":
        assert allowed.status_code == 200, allowed.text
    else:
        _assert_refused(allowed, "insights", REASONS[state])
    for label in ("unknown", "revoked", "expired"):
        refused = app_client.get(issues, headers=bearers[label])
        assert refused.status_code == 401, (label, refused.text)
        assert _code(refused) == "api_key_invalid", (label, refused.text)
    foreign = app_client.get(issues, headers=bearers["valid, foreign workspace header"])
    assert foreign.status_code == 403, foreign.text
    assert _code(foreign) == "workspace_access_denied", foreign.text


def test_session_only_trends_never_spend_the_key_rate_limit(
    app_client, settings, email_outbox, monkeypatch
):
    owner = _signup(app_client, email_outbox, "key-rate")
    key_headers = _bearer(_mint_key(app_client, owner, "key-rate")["key"])
    app_client.cookies.clear()
    monkeypatch.setattr(settings, "api_key_rate_limit_per_hour", 2)

    for round_ in range(2):
        for path in SESSION_ONLY_TRENDS:
            _assert_key_not_accepted(
                app_client.get(path, headers=key_headers), ("under", round_, path)
            )
    issues = f"{API}/trends/issues"
    for round_ in range(2):
        allowed = app_client.get(issues, headers=key_headers)
        assert allowed.status_code == 200, (round_, allowed.text)
    limited = app_client.get(issues, headers=key_headers)
    assert limited.status_code == 429, limited.text
    assert _code(limited) == "api_key_rate_limited", limited.text

    for path in SESSION_ONLY_TRENDS:
        _assert_key_not_accepted(app_client.get(path, headers=key_headers), ("over", path))


# --- guard inventory ----------------------------------------------------------------


def test_every_module_route_carries_exactly_one_guard_and_core_routes_none(app_client):
    counts: dict[str, int] = {}
    seen_operator: dict[tuple[str, str], int] = {}
    new_routes: set[tuple[str, str]] = set()
    problems: list[str] = []
    for route in app_client.app.routes:
        if not isinstance(route, APIRoute):
            continue
        owner_module = route.endpoint.__module__
        guards = _module_guards(route)
        for method in sorted(route.methods):
            key = _route_key(route, method)
            if key == ("GET", "/capabilities") or (
                method == "PUT" and route.path.startswith(f"{ADMIN_CAPABILITIES}/{{")
            ):
                new_routes.add(key)
            if owner_module in ROUTER_MODULES:
                counts[owner_module] = counts.get(owner_module, 0) + 1
            if key in OPERATOR_ROUTES:
                seen_operator[key] = seen_operator.get(key, 0) + 1
                expected: list[str] = []
            elif owner_module in ROUTER_MODULES:
                expected = [ROUTER_MODULES[owner_module]]
            else:
                expected = []
            if guards != expected:
                problems.append(f"{method} {route.path} ({owner_module}): {guards} != {expected}")

    assert problems == []
    for owner_module, base_count in BASE_ROUTE_COUNTS.items():
        assert counts.get(owner_module, 0) >= base_count, owner_module
    assert counts["backend.routers.v1.voice"] == 8
    assert seen_operator == {key: 1 for key in OPERATOR_ROUTES}
    assert len(new_routes) == 2, new_routes


def test_voice_routes_keep_path_method_model_and_policy(app_client):
    found: dict[tuple[str, str], APIRoute] = {}
    for route in app_client.app.routes:
        if isinstance(route, APIRoute) and route.path.startswith(f"{API}/voice/"):
            for method in route.methods:
                found[_route_key(route, method)] = route

    assert set(found) == set(OPERATOR_ROUTES) | VOICE_TENANT_ROUTES
    for key, model in OPERATOR_ROUTES.items():
        route = found[key]
        assert route.response_model.__name__ == model
        assert _permission_policies(route) == [("workspace:manage", True, True)], key


def test_module_guard_sits_on_every_module_router(app_client):
    problems: list[str] = []
    for owner_module, key in ROUTER_MODULES.items():
        module = importlib.import_module(owner_module)
        routers = [value for value in vars(module).values() if isinstance(value, APIRouter)]
        assert routers, owner_module
        for router in routers:
            own = {
                (method, route.path)
                for route in router.routes
                if isinstance(route, APIRoute)
                for method in route.methods
            }
            guards = [
                getattr(dependency.dependency, "homayar_module", None)
                for dependency in router.dependencies
            ]
            guards = [guard for guard in guards if guard is not None]
            expected = [] if own and own <= set(OPERATOR_ROUTES) else [key]
            if guards != expected:
                problems.append(f"{owner_module} router {sorted(own)[:2]}: {guards} != {expected}")
    assert problems == []
