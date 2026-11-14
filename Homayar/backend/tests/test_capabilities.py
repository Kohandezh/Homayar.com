"""HOM-229: the capability registry, the module guard and the capability endpoints.

Registry data and lookup, the install/license/admin effective-state rules (including
every fail-closed case), one guard per module router (inventoried from ``app.routes``,
never hand-listed; the session-only trends views are pinned to the session-only
workspace of their guard), the per-module refusal states on a representative read and
write route, workspace isolation of the admin switch, data kept across off/on, the two
endpoints (roles, CSRF, strict body, audit) and the ``DISABLED_MODULES`` setting.
"""

from __future__ import annotations

import copy

import pytest
from fastapi.params import Depends as ParamDepends
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from backend.config import Settings
from backend.database.saas_models import (
    AuditEvent,
    CoachingTask,
    IntegrationMappingProfile,
    Membership,
    Notification,
    ScorecardVersion,
    Subscription,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.routers.v1.trends import require_session_workspace
from backend.security.context import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    require_workspace,
)
from backend.services.capability_registry import (
    CAPABILITY_MODULES,
    CAPABILITY_REGISTRY_VERSION,
    admin_module_enabled,
    licensed_module_keys,
    lookup_module,
    parse_disabled_modules,
)
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, pin_negotiated_plan, register_user
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_coaching_tasks_api import create as coaching_create
from backend.tests.test_review_appeals_api import build_appeal_queue, reviewed
from backend.tests.test_uploads_api import _valid_metadata
from backend.voicemail.models import VoicemailMessage

CAPABILITIES = "/api/v1/capabilities"
MODULE_KEYS = ("quality", "insights", "copilot", "voice", "voicemail", "integrations")
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


# --- helpers -----------------------------------------------------------------------------


def _cookies(client) -> dict[str, str]:
    return {
        name: value
        for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
        if (value := client.cookies.get(name))
    }


class Caller:
    """A signed-in user able to act on any workspace they belong to."""

    def __init__(self, client, cookies: dict[str, str]):
        self.client = client
        self.cookies = cookies

    def headers(self, workspace_id: str, *, write: bool = True) -> dict[str, str]:
        headers = {"X-Workspace-ID": workspace_id}
        if write:
            headers["X-CSRF-Token"] = self.cookies[CSRF_COOKIE]
        return headers

    def request(self, workspace_id: str, method: str, path: str, **kwargs):
        return self.client.request(
            method,
            path,
            cookies=self.cookies,
            headers=self.headers(workspace_id, write=method.upper() != "GET"),
            **kwargs,
        )

    def get(self, workspace_id: str, path: str, **kwargs):
        return self.request(workspace_id, "GET", path, **kwargs)

    def put_module(self, workspace_id: str, key: str, enabled: bool):
        return self.request(
            workspace_id,
            "PUT",
            f"/api/v1/admin/capabilities/{key}",
            json={"enabled": enabled},
        )


def _caller(client, email_outbox, *, email: str) -> tuple[Caller, str, str]:
    register_user(client, email_outbox, email=email, name=email.split("@")[0])
    login_user(client, {"email": email, "password": "SecurePass123"})
    cookies = _cookies(client)
    with inspection_session_factory()() as db:
        user = db.scalar(select(User).where(User.email == email))
        workspace_id = db.scalar(
            select(Membership.workspace_id)
            .where(Membership.user_id == user.id)
            .order_by(Membership.created_at.asc())
        )
    return Caller(client, cookies), user.id, workspace_id


def _add_membership(*, workspace_id: str, user_id: str, role: WorkspaceRole) -> None:
    with inspection_session_factory()() as db:
        db.add(Membership(workspace_id=workspace_id, user_id=user_id, role=role))
        db.commit()


def _error(response) -> dict:
    return response.json()["error"]


def _code(response) -> str:
    return response.json()["error"]["code"]


def _refusal(response, module: str, reason: str) -> None:
    assert response.status_code == 403, response.text
    error = _error(response)
    assert error["code"] == "module_disabled", response.text
    assert error["details"]["module"] == module
    assert error["details"]["reason"] == reason


def _mutate_settings(workspace_id: str, mutator) -> None:
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, workspace_id)
        settings_json = copy.deepcopy(dict(workspace.settings_json or {}))
        mutator(settings_json)
        workspace.settings_json = settings_json
        flag_modified(workspace, "settings_json")
        db.commit()


def _stored_settings(workspace_id: str) -> dict:
    with inspection_session_factory()() as db:
        return copy.deepcopy(dict(db.get(Workspace, workspace_id).settings_json or {}))


def _store_raw_settings(workspace_id: str, value) -> None:
    """Write ``settings_json`` verbatim, so a test can store a non-object."""
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, workspace_id)
        workspace.settings_json = value
        flag_modified(workspace, "settings_json")
        db.commit()


def _stored_raw_settings(workspace_id: str):
    with inspection_session_factory()() as db:
        return copy.deepcopy(db.get(Workspace, workspace_id).settings_json)


def _rows(*models) -> dict[str, int]:
    with inspection_session_factory()() as db:
        return {
            model.__name__: db.scalar(select(func.count()).select_from(model)) for model in models
        }


def _rows_without_module_toggles(*models) -> dict[str, int]:
    """Like ``_rows``, but the toggle's own ``workspace.module.*`` audits are left
    out: the module states helper flips the switch between the two counts."""
    with inspection_session_factory()() as db:
        result = {}
        for model in models:
            if model is AuditEvent:
                result[model.__name__] = db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.event_type.notin_(
                            ("workspace.module.enabled", "workspace.module.disabled")
                        )
                    )
                )
            else:
                result[model.__name__] = db.scalar(select(func.count()).select_from(model))
        return result


def _make_operator(email: str) -> None:
    with inspection_session_factory()() as db:
        user = db.scalar(select(User).where(User.email == email))
        user.system_role = "operator"
        db.commit()


@pytest.fixture
def vm_allowed(client, monkeypatch):
    monkeypatch.setattr(
        "backend.voicemail.ingest.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=42.3),
    )
    monkeypatch.setattr(
        "backend.voicemail.ingest.dispatch_job",
        lambda job_id, _settings: True,
    )
    return client


@pytest.fixture
def voice_plane(client):
    from backend.routers.v1 import voice as voice_router
    from backend.voice.admission import AdmissionPolicy, VoiceControlPlane
    from backend.voice.metrics import VoiceMetrics

    plane = VoiceControlPlane(
        AdmissionPolicy(global_limit=200, default_workspace_limit=2), metrics=VoiceMetrics()
    )
    client.app.dependency_overrides[voice_router.get_voice_control_plane] = lambda: plane
    yield client
    client.app.dependency_overrides.pop(voice_router.get_voice_control_plane, None)


def _import_vm(caller: Caller, workspace_id: str, *, external_id: str = "vm-cap-1"):
    return caller.request(
        workspace_id,
        "POST",
        "/api/v1/voicemails",
        data={
            "external_message_id": external_id,
            "caller_number": "0912 345 6789",
            "mailbox": "sales",
        },
        files={"file": ("vm.wav", b"capability wav bytes", "audio/wav")},
    )


def _scorecard_body(expected: int = 0, threshold: int = 80) -> dict:
    return {
        "definition": {
            "name": "Inbound sales",
            "pass_threshold": threshold,
            "criteria": [
                {
                    "criterion_id": "greeting",
                    "label": "Greets the customer",
                    "weight": 100,
                    "check": {
                        "check_id": "greeting",
                        "version": 1,
                        "kind": "required_phrase",
                        "phrases": ["hello"],
                    },
                }
            ],
        },
        "reason": "capability test",
        "expected_version": expected,
    }


def _mapping_profile() -> dict:
    return {
        "conversation_source_system": "issabel",
        "field_map": {
            "conversation_id": "call.id",
            "customer_id": "customer.id",
            "ticket_id": "ticket.id",
            "agent_id": "agent.id",
            "outcome": "result.code",
            "customer_active": "customer.active",
            "ticket_active": "ticket.active",
        },
        "outcome_map": {"WON": "won", "LOST": "lost"},
    }


# --- 1. the registry ---------------------------------------------------------------------


def test_registry_is_version_one_with_the_six_modules_in_order():
    assert CAPABILITY_REGISTRY_VERSION == 1
    assert [module.key for module in CAPABILITY_MODULES] == list(MODULE_KEYS)
    assert {module.key: module.name for module in CAPABILITY_MODULES} == {
        "quality": "Quality",
        "insights": "Insights",
        "copilot": "Copilot",
        "voice": "Voice",
        "voicemail": "Voicemail",
        "integrations": "Integrations",
    }
    for module in CAPABILITY_MODULES:
        assert module.description
        assert module.key.isascii() and module.key.islower() and module.key.isidentifier()


def test_module_lookup_is_exact():
    assert lookup_module("quality").key == "quality"
    for unknown in ("Voice", " voice", "voice ", "VOICE", "qualty", "", "quality.insights", None):
        assert lookup_module(unknown) is None, unknown


# --- 2. the install setting --------------------------------------------------------------


def test_disabled_modules_setting_parses_trims_and_ignores_empty_items():
    assert parse_disabled_modules("") == frozenset()
    assert parse_disabled_modules(" , ,") == frozenset()
    assert parse_disabled_modules(" voice ,,voicemail , quality ") == frozenset(
        {"voice", "voicemail", "quality"}
    )


def test_disabled_modules_setting_rejects_unknown_keys():
    for bad in ("Voice", "voicee", " voice ,typo"):
        with pytest.raises(ValueError):
            parse_disabled_modules(bad)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="test", disabled_modules="typo")
    assert Settings(_env_file=None, environment="test", disabled_modules=" voice ,copilot")


# --- 3. the license and admin-switch readers ---------------------------------------------


def test_license_reader_covers_every_shape():
    every = frozenset(MODULE_KEYS)
    assert licensed_module_keys({}) is None
    assert licensed_module_keys({"seats": 5}) is None
    assert licensed_module_keys({"modules": ["voice", "typo"]}) == frozenset({"voice"})
    assert licensed_module_keys({"modules": []}) == frozenset()
    for malformed in (None, "voice", {"a": 1}, ["voice", 5], [True]):
        assert licensed_module_keys({"modules": malformed}) == frozenset()
    assert licensed_module_keys({"modules": list(MODULE_KEYS)}) == every
    # entitlements that are not a JSON object license nothing (amendment 2)
    for not_an_object in (None, ["voice"], ["modules"], "voice", 5, True):
        assert licensed_module_keys(not_an_object) == frozenset()


def test_admin_switch_reader_covers_every_shape():
    assert admin_module_enabled(None, "voice") is True
    assert admin_module_enabled({}, "voice") is True
    assert admin_module_enabled({"modules": {}}, "voice") is True
    assert admin_module_enabled({"modules": {"voice": {"enabled": True}}}, "voice") is True
    assert admin_module_enabled({"modules": {"voice": {"enabled": False}}}, "voice") is False
    # readers ignore audit-useful extras but refuse an unreadable entry
    assert (
        admin_module_enabled(
            {
                "modules": {
                    "voice": {"enabled": False, "changed_at": "2026-01-01", "changed_by": "u"}
                }
            },
            "voice",
        )
        is False
    )
    for unreadable in ("false", "no", 0, 1, None):
        assert (
            admin_module_enabled({"modules": {"voice": {"enabled": unreadable}}}, "voice") is False
        )
    assert admin_module_enabled({"modules": {"voice": {}}}, "voice") is False
    assert admin_module_enabled({"modules": {"voice": "off"}}, "voice") is False
    for other in ("quality", "voicemail"):
        assert admin_module_enabled({"modules": {"voice": {"enabled": False}}}, other) is True
    # "modules" itself unreadable: every module is refused
    for key in MODULE_KEYS:
        assert admin_module_enabled({"modules": ["voice"]}, key) is False
        assert admin_module_enabled({"modules": "nope"}, key) is False
    # settings that are not an object at all refuse every module the same way
    # (amendment 3); no settings at all is the default enabled state
    for key in MODULE_KEYS:
        assert admin_module_enabled(None, key) is True
        for whole in (["voice"], "nope", 5, True):
            assert admin_module_enabled(whole, key) is False


# --- 4. one guard per module router, inventoried from the app ----------------------------

VOICE_OPERATOR_ROUTES = {
    ("POST", "/api/v1/voice/nodes"),
    ("POST", "/api/v1/voice/nodes/{node_id}/heartbeat"),
    ("POST", "/api/v1/voice/nodes/{node_id}/drain"),
    ("POST", "/api/v1/voice/recover"),
}

MODULE_ROUTE_PREFIXES = (
    ("/api/v1/scorecards", "quality"),
    ("/api/v1/review-items", "quality"),
    ("/api/v1/calibration-batches", "quality"),
    ("/api/v1/my-reviews", "quality"),
    ("/api/v1/my-appeals", "quality"),
    ("/api/v1/review-appeals", "quality"),
    ("/api/v1/coaching-tasks", "quality"),
    ("/api/v1/my-coaching-tasks", "quality"),
    ("/api/v1/trends", "insights"),
    ("/api/v1/voicemails", "voicemail"),
    ("/api/v1/integrations", "integrations"),
    ("/api/v1/webhooks", "integrations"),
    ("/api/v1/action-proposals", "integrations"),
)


def _expected_module(method: str, path: str) -> str | None:
    if path.startswith("/api/v1/voice/"):
        if (method, path) in VOICE_OPERATOR_ROUTES:
            return None
        return "voice"
    for prefix, key in MODULE_ROUTE_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return key
    return None


def _module_guard_calls(route: APIRoute) -> list:
    """The route's module guard callables (``require_module``'s closures)."""
    found: list = []

    def walk(dependant) -> None:
        if getattr(dependant.call, "homayar_module", None) is not None:
            found.append(dependant.call)
            return
        for sub in dependant.dependencies:
            walk(sub)

    for sub in route.dependant.dependencies:
        walk(sub)
    return found


def _module_guards(route: APIRoute) -> list[str]:
    return [guard.homayar_module for guard in _module_guard_calls(route)]


def _session_only_policies(route: APIRoute) -> list:
    """The route's policy dependencies marked ``homayar_session_only`` (HOM-82)."""
    found: list = []

    def walk(dependant) -> None:
        if getattr(dependant.call, "homayar_session_only", False) is True:
            found.append(dependant.call)
            return
        for sub in dependant.dependencies:
            walk(sub)

    for sub in route.dependant.dependencies:
        walk(sub)
    return found


def _guard_workspace_dependency(guard) -> object:
    """The workspace callable a module guard resolves the workspace through.

    ``require_module``'s guard closes over its ``workspace`` parameter as the
    ``Depends`` default of its ``context`` argument, so the callable is read
    back from the closure's defaults."""
    candidates = [
        default.dependency
        for default in guard.__defaults__ or ()
        if isinstance(default, ParamDepends)
        and default.dependency in (require_workspace, require_session_workspace)
    ]
    assert len(candidates) == 1, guard.homayar_module
    return candidates[0]


def test_every_route_carries_exactly_the_module_guard_it_should(client):
    checked = 0
    for route in client.app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            expected = _expected_module(method, route.path)
            guards = _module_guards(route)
            if expected is None:
                assert guards == [], (method, route.path, guards)
            else:
                assert guards == [expected], (method, route.path, guards)
            checked += 1
    assert checked > 100  # the whole mounted app was inventoried
    for method, path in VOICE_OPERATOR_ROUTES:
        assert any(
            isinstance(route, APIRoute) and route.path == path and method in route.methods
            for route in client.app.routes
        ), (method, path)


def test_session_only_routes_take_their_guard_workspace_from_the_session(client):
    """Amendment 2, pinned: every route whose policy carries
    ``homayar_session_only`` must sit behind the module guard that resolves its
    workspace from ``require_session_workspace`` (the session-only resolution,
    which refuses every API-key bearer before any key lookup), never from
    ``require_workspace`` (the key-accepting resolution).  A session-only route
    declared on the key-accepting trends ``router`` fails here."""
    seen = set()
    for route in client.app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not _session_only_policies(route):
            continue
        guards = _module_guard_calls(route)
        assert len(guards) == 1, (route.path, _module_guards(route))
        workspace = _guard_workspace_dependency(guards[0])
        assert workspace is require_session_workspace, (route.path, workspace)
        assert workspace is not require_workspace, route.path
        seen.add(route.path)
    assert seen == {
        "/api/v1/trends/teams",
        "/api/v1/trends/me",
        "/api/v1/trends/conversations",
        "/api/v1/action-proposals",
        "/api/v1/action-proposals/{proposal_id}",
        "/api/v1/action-proposals/{proposal_id}/refresh",
        "/api/v1/action-proposals/{proposal_id}/approve",
        "/api/v1/action-proposals/{proposal_id}/reject",
        "/api/v1/action-proposals/{proposal_id}/revoke",
    }


# --- 5. the GET endpoint -----------------------------------------------------------------


def test_get_lists_every_module_in_registry_order_for_any_member(client, email_outbox):
    caller, _, workspace_id = _caller(client, email_outbox, email="cap-owner@example.com")
    viewer = _caller(client, email_outbox, email="cap-viewer@example.com")
    _add_membership(workspace_id=workspace_id, user_id=viewer[1], role=WorkspaceRole.VIEWER)

    response = caller.get(workspace_id, CAPABILITIES)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"registry_version", "workspace_id", "modules"}
    assert body["registry_version"] == 1
    assert body["workspace_id"] == workspace_id
    assert [module["key"] for module in body["modules"]] == list(MODULE_KEYS)
    for module in body["modules"]:
        assert set(module) == MODULE_FIELDS
        assert module["enabled"] is True
        assert module["reason"] is None
        assert module["install_enabled"] is True
        assert module["licensed"] is True
        assert module["admin_enabled"] is True

    # every member reads it: a viewer too, and an API key
    assert viewer[0].get(workspace_id, CAPABILITIES).status_code == 200
    owner_headers = {"X-Workspace-ID": workspace_id, "X-CSRF-Token": caller.cookies[CSRF_COOKIE]}
    minted = _mint(client, owner_headers, caller.cookies, name="cap-read", scopes=["meeting:read"])
    assert minted.status_code == 201, minted.text
    keyed = client.get(
        CAPABILITIES, headers=_bearer(minted.json()["key"], **{"X-Workspace-ID": workspace_id})
    )
    assert keyed.status_code == 200, keyed.text

    client.cookies.clear()
    assert client.get(CAPABILITIES, headers={"X-Workspace-ID": workspace_id}).status_code == 401


def test_get_is_never_refused_by_module_guards(client, email_outbox):
    caller, _, workspace_id = _caller(client, email_outbox, email="cap-all-off@example.com")
    for key in MODULE_KEYS:
        response = caller.put_module(workspace_id, key, False)
        assert response.status_code == 200, response.text
    listed = caller.get(workspace_id, CAPABILITIES)
    assert listed.status_code == 200, listed.text
    assert [module["enabled"] for module in listed.json()["modules"]] == [False] * 6
    assert all(module["reason"] == "disabled_by_admin" for module in listed.json()["modules"])
    assert all(module["admin_enabled"] is False for module in listed.json()["modules"])


def test_get_reports_fail_closed_and_no_subscription_states(client, email_outbox):
    caller, _, workspace_id = _caller(client, email_outbox, email="cap-shapes@example.com")
    _mutate_settings(
        workspace_id, lambda data: data.update({"modules": {"voicemail": {"enabled": "no"}}})
    )
    listed = caller.get(workspace_id, CAPABILITIES).json()
    voicemail = next(m for m in listed["modules"] if m["key"] == "voicemail")
    assert voicemail["admin_enabled"] is False
    assert voicemail["enabled"] is False
    assert voicemail["reason"] == "disabled_by_admin"
    _refusal(caller.get(workspace_id, "/api/v1/voicemails"), "voicemail", "disabled_by_admin")

    _mutate_settings(
        workspace_id, lambda data: data.update({"modules": {"voicemail": {"enabled": True}}})
    )
    pin_negotiated_plan(workspace_id, "negotiated_null_modules", {"modules": None})
    listed = caller.get(workspace_id, CAPABILITIES).json()
    assert all(
        module["licensed"] is False and module["enabled"] is False for module in listed["modules"]
    )
    assert all(module["reason"] == "not_licensed" for module in listed["modules"])

    # no current subscription licenses every module (the HOM-109 gap)
    with inspection_session_factory()() as db:
        for subscription in db.scalars(
            select(Subscription).where(Subscription.workspace_id == workspace_id)
        ):
            db.delete(subscription)
        db.commit()
    listed = caller.get(workspace_id, CAPABILITIES).json()
    assert all(
        module["licensed"] is True and module["enabled"] is True for module in listed["modules"]
    )
    assert caller.get(workspace_id, "/api/v1/voicemails").status_code == 200


# --- 6. the PUT endpoint -----------------------------------------------------------------


def test_put_roles_api_key_and_csrf(client, email_outbox):
    caller, _, workspace_id = _caller(client, email_outbox, email="cap-roles@example.com")
    admin = _caller(client, email_outbox, email="cap-admin@example.com")
    member = _caller(client, email_outbox, email="cap-member@example.com")
    viewer = _caller(client, email_outbox, email="cap-viewer2@example.com")
    _add_membership(workspace_id=workspace_id, user_id=admin[1], role=WorkspaceRole.ADMIN)
    _add_membership(workspace_id=workspace_id, user_id=member[1], role=WorkspaceRole.MEMBER)
    _add_membership(workspace_id=workspace_id, user_id=viewer[1], role=WorkspaceRole.VIEWER)

    denied = member[0].put_module(workspace_id, "voice", False)
    assert denied.status_code == 403 and _code(denied) == "permission_denied"
    denied = viewer[0].put_module(workspace_id, "voice", False)
    assert denied.status_code == 403 and _code(denied) == "permission_denied"

    # workspace:manage is never an API-key scope
    owner_headers = {"X-Workspace-ID": workspace_id, "X-CSRF-Token": caller.cookies[CSRF_COOKIE]}
    minted = _mint(client, owner_headers, caller.cookies, name="cap-put", scopes=["meeting:read"])
    keyed = client.put(
        "/api/v1/admin/capabilities/voice",
        json={"enabled": False},
        headers=_bearer(minted.json()["key"], **{"X-Workspace-ID": workspace_id}),
    )
    assert keyed.status_code == 403 and _code(keyed) == "api_key_scope_denied"

    # cookie sessions prove CSRF
    no_csrf = client.put(
        "/api/v1/admin/capabilities/voice",
        json={"enabled": False},
        cookies=caller.cookies,
        headers={"X-Workspace-ID": workspace_id},
    )
    assert no_csrf.status_code == 403 and _code(no_csrf) == "csrf_failed"

    for actor in (caller, admin[0]):
        response = actor.put_module(workspace_id, "voice", False)
        assert response.status_code == 200, response.text
        assert response.json()["key"] == "voice"
        assert response.json()["admin_enabled"] is False
        assert response.json()["reason"] == "disabled_by_admin"


def test_put_body_is_strict_and_unknown_keys_are_404(client, email_outbox):
    caller, _, workspace_id = _caller(client, email_outbox, email="cap-strict@example.com")
    for body in (
        {},
        {"enabled": "true"},
        {"enabled": "false"},
        {"enabled": 1},
        {"enabled": 0},
        {"enabled": None},
        {"enabled": True, "extra": 1},
    ):
        response = caller.request(
            workspace_id, "PUT", "/api/v1/admin/capabilities/voice", json=body
        )
        assert response.status_code == 422, (body, response.text)
        assert _code(response) == "validation_error"

    for key in ("Voice", "VOICE", " voice", "voic", "voice ", "qualityx", "modules"):
        response = caller.put_module(workspace_id, key, True)
        assert response.status_code == 404, (key, response.text)
        assert _code(response) == "module_not_found"

    # copilot has no routes but is a registry module: the switch is stored
    response = caller.put_module(workspace_id, "copilot", False)
    assert response.status_code == 200, response.text
    assert response.json()["key"] == "copilot"
    assert caller.get(workspace_id, CAPABILITIES).json()["modules"][2]["admin_enabled"] is False


def test_put_writes_audit_and_keeps_every_other_settings_key(client, email_outbox):
    caller, owner_id, workspace_id = _caller(client, email_outbox, email="cap-audit@example.com")
    _mutate_settings(
        workspace_id,
        lambda data: data.update(
            {
                "channel_role_mappings": {"issabel:issabel": {"version": 3}},
                "unrelated": {"keep": [1]},
            }
        ),
    )

    before = _rows(AuditEvent)
    off = caller.put_module(workspace_id, "voicemail", False)
    assert off.status_code == 200, off.text
    on = caller.put_module(workspace_id, "voicemail", True)
    assert on.status_code == 200, on.text
    same = caller.put_module(workspace_id, "voicemail", True)
    assert same.status_code == 200, same.text

    with inspection_session_factory()() as db:
        events = list(
            db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.workspace_id == workspace_id,
                    AuditEvent.event_type.in_(
                        ("workspace.module.enabled", "workspace.module.disabled")
                    ),
                )
                .order_by(AuditEvent.occurred_at, AuditEvent.id)
            )
        )
        assert [event.event_type for event in events] == [
            "workspace.module.disabled",
            "workspace.module.enabled",
            "workspace.module.enabled",
        ]
        for event in events:
            assert event.actor_user_id == owner_id
            assert event.target_type == "workspace"
            assert event.target_id == workspace_id
            assert event.details == {
                "module": "voicemail",
                "enabled": event.event_type.endswith("enabled"),
            }

    assert _rows(AuditEvent)["AuditEvent"] == before["AuditEvent"] + 3
    stored = _stored_settings(workspace_id)
    assert stored["unrelated"] == {"keep": [1]}
    assert stored["channel_role_mappings"] == {"issabel:issabel": {"version": 3}}
    assert stored["modules"]["voicemail"]["enabled"] is True


def test_put_is_allowed_whatever_the_license_and_install_state(client, email_outbox, monkeypatch):
    caller, _, workspace_id = _caller(client, email_outbox, email="cap-put-license@example.com")
    pin_negotiated_plan(workspace_id, "negotiated_no_voice", {"modules": ["quality"]})
    monkeypatch.setattr(client.app.state.settings, "disabled_modules", "voicemail")

    response = caller.put_module(workspace_id, "voicemail", False)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["key"] == "voicemail"
    assert body["admin_enabled"] is False
    assert body["licensed"] is False
    assert body["install_enabled"] is False
    assert body["enabled"] is False
    assert body["reason"] == "disabled_for_install"  # the first refusing input wins

    assert _stored_settings(workspace_id)["modules"]["voicemail"]["enabled"] is False


def _assert_all_refused(caller: Caller, workspace_id: str) -> None:
    listed = caller.get(workspace_id, CAPABILITIES).json()
    for module in listed["modules"]:
        assert module["admin_enabled"] is False, module
        assert module["enabled"] is False, module
        assert module["reason"] == "disabled_by_admin", module
    _refusal(caller.get(workspace_id, "/api/v1/scorecards"), "quality", "disabled_by_admin")


def test_put_refuses_unreadable_stored_module_settings(client, email_outbox):
    """Amendment 3: the write never repairs an unreadable stored switch.  A
    present ``"modules"`` key that is not an object (a list, a string, null)
    and a ``settings_json`` that is not an object at all each refuse the PUT
    with 409 ``module_settings_unreadable``: the stored value is unchanged, no
    audit event is written, and GET still shows all six modules refused."""
    caller, _, workspace_id = _caller(client, email_outbox, email="cap-unreadable@example.com")

    for value in (["voice"], "nope", None):
        _mutate_settings(workspace_id, lambda data, value=value: data.update({"modules": value}))
        before = _rows(AuditEvent)
        for key in ("voice", "quality"):
            refused = caller.put_module(workspace_id, key, False)
            assert refused.status_code == 409, (value, key, refused.text)
            assert _code(refused) == "module_settings_unreadable"
        assert _rows(AuditEvent) == before  # a refused write audits nothing
        assert _stored_settings(workspace_id)["modules"] == value
        _assert_all_refused(caller, workspace_id)

    _store_raw_settings(workspace_id, ["voice"])
    before = _rows(AuditEvent)
    refused = caller.put_module(workspace_id, "quality", False)
    assert refused.status_code == 409, refused.text
    assert _code(refused) == "module_settings_unreadable"
    assert _rows(AuditEvent) == before
    assert _stored_raw_settings(workspace_id) == ["voice"]
    _assert_all_refused(caller, workspace_id)

    # control: a readable object "modules" accepts the write again
    _store_raw_settings(workspace_id, {"modules": {}})
    accepted = caller.put_module(workspace_id, "voice", False)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["admin_enabled"] is False
    assert _stored_settings(workspace_id)["modules"]["voice"]["enabled"] is False


def test_put_keeps_another_modules_unreadable_entry_and_replaces_its_own(client, email_outbox):
    caller, user_id, workspace_id = _caller(client, email_outbox, email="cap-entry@example.com")
    _mutate_settings(workspace_id, lambda data: data.update({"modules": {"voice": "x"}}))
    listed = caller.get(workspace_id, CAPABILITIES).json()
    by_key = {module["key"]: module for module in listed["modules"]}
    assert by_key["voice"]["admin_enabled"] is False  # the unreadable entry refuses voice
    assert all(by_key[key]["admin_enabled"] is True for key in MODULE_KEYS if key != "voice")

    # a write for another module keeps the unreadable voice entry unchanged
    replaced = caller.put_module(workspace_id, "quality", False)
    assert replaced.status_code == 200, replaced.text
    modules = _stored_settings(workspace_id)["modules"]
    assert modules["voice"] == "x"
    assert modules["quality"]["enabled"] is False
    listed = caller.get(workspace_id, CAPABILITIES).json()
    by_key = {module["key"]: module for module in listed["modules"]}
    assert by_key["quality"]["admin_enabled"] is False
    assert by_key["voice"]["admin_enabled"] is False  # still the unreadable entry

    # a write for the module itself replaces its unreadable entry
    own = caller.put_module(workspace_id, "voice", True)
    assert own.status_code == 200, own.text
    assert own.json()["key"] == "voice"
    assert own.json()["admin_enabled"] is True
    entry = _stored_settings(workspace_id)["modules"]["voice"]
    assert entry["enabled"] is True
    assert entry["changed_by"] == user_id


# --- 7. per-module refusal states on a representative read and write route ----------------


def _subscription_pin(workspace_id: str):
    with inspection_session_factory()() as db:
        subscription = db.scalar(
            select(Subscription).where(Subscription.workspace_id == workspace_id)
        )
        return (subscription.plan_key, subscription.plan_version) if subscription else None


def _restore_pin(workspace_id: str, pin: tuple[str, int] | None) -> None:
    if pin is None:
        return
    from sqlalchemy import update

    with inspection_session_factory()() as db:
        db.execute(
            update(Subscription)
            .where(Subscription.workspace_id == workspace_id)
            .values(plan_key=pin[0], plan_version=pin[1])
        )
        db.commit()


def _check_module_states(
    client,
    email_outbox,
    *,
    email: str,
    module: str,
    read,
    write,
    counted,
):
    """``read(caller, ws)``/``write(caller, ws)`` return responses; ``counted()``
    returns the counts a refused write must not change.  Returns the caller, its
    user id, its workspace and its email, with the license and admin switch back
    to where they started."""
    caller, user_id, workspace_id = _caller(client, email_outbox, email=email)
    original_pin = _subscription_pin(workspace_id)

    assert read(caller, workspace_id).status_code == 200
    assert write(caller, workspace_id).status_code < 400
    counts_after_work = counted()

    caller.put_module(workspace_id, module, False)
    _refusal(read(caller, workspace_id), module, "disabled_by_admin")
    _refusal(write(caller, workspace_id), module, "disabled_by_admin")
    assert counted() == counts_after_work  # a refused write creates nothing

    caller.put_module(workspace_id, module, True)
    assert read(caller, workspace_id).status_code == 200

    pin_negotiated_plan(workspace_id, f"negotiated_only_{module}", {"modules": ["quality"]})
    if module == "quality":
        assert read(caller, workspace_id).status_code == 200
    else:
        _refusal(read(caller, workspace_id), module, "not_licensed")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(client.app.state.settings, "disabled_modules", module)
        _refusal(read(caller, workspace_id), module, "disabled_for_install")
        _refusal(write(caller, workspace_id), module, "disabled_for_install")
        assert client.app.state.settings.disabled_module_keys == frozenset({module})

    _restore_pin(workspace_id, original_pin)
    return caller, user_id, workspace_id, email


def test_quality_module_states(client, email_outbox):
    def read(caller, ws):
        return caller.get(ws, "/api/v1/scorecards")

    def write(caller, ws):
        return caller.request(
            ws, "POST", "/api/v1/scorecards/cap_quality/versions", json=_scorecard_body()
        )

    def counted():
        return _rows_without_module_toggles(ScorecardVersion, AuditEvent)

    caller, user_id, workspace_id, _ = _check_module_states(
        client,
        email_outbox,
        email="cap-quality@example.com",
        module="quality",
        read=read,
        write=write,
        counted=counted,
    )

    # data kept: the published scorecard comes back unchanged after off/on
    listed = caller.get(workspace_id, "/api/v1/scorecards").json()["items"]
    assert [item["scorecard_key"] for item in listed] == ["cap_quality"]
    history = caller.get(workspace_id, "/api/v1/scorecards/cap_quality/versions").json()["items"]
    assert [item["version"] for item in history] == [1]
    assert _rows(ScorecardVersion)["ScorecardVersion"] == 1


def test_quality_module_states_on_the_coaching_routes(client, email_outbox, monkeypatch):
    """Amendment 4: HOM-77's coaching router follows the ``quality`` module. The
    findings are built with HOM-77's own queue fixtures (``sup`` completes the
    review, ``off_topic`` stays the failed finding); both findings exist before
    the module is turned off, because the review routes are guarded too."""
    queue = build_appeal_queue(client, email_outbox, monkeypatch)
    owner = Caller(client, queue.owner.cookies)
    coached = reviewed(queue)  # becomes an active task while the module is on
    fresh = reviewed(queue)  # the finding the refused create targets
    tasks = "/api/v1/coaching-tasks"

    def read():
        return queue.sup.get(tasks)

    def write():
        return coaching_create(queue.sup, fresh.id, queue.plain)

    def counted():
        return _rows_without_module_toggles(CoachingTask, Notification, AuditEvent)

    # enabled: the normal answers
    assert read().status_code == 200
    accepted = coaching_create(queue.sup, coached.id, queue.plain)
    assert accepted.status_code == 201, accepted.text
    after_work = counted()

    # disabled by the workspace admin: both routes refused, the refused create
    # writes no coaching task, no notification and no audit event
    assert owner.put_module(queue.workspace_id, "quality", False).status_code == 200
    _refusal(read(), "quality", "disabled_by_admin")
    _refusal(write(), "quality", "disabled_by_admin")
    assert counted() == after_work

    # enabled again: the same finding still takes its task (nothing was created)
    assert owner.put_module(queue.workspace_id, "quality", True).status_code == 200
    assert read().status_code == 200
    assert write().status_code == 201
    assert counted()["CoachingTask"] == after_work["CoachingTask"] + 1

    # not licensed: the same refusal with the license reason
    original_pin = _subscription_pin(queue.workspace_id)
    pin_negotiated_plan(
        queue.workspace_id, "negotiated_coaching_quality_off", {"modules": ["insights"]}
    )
    _refusal(read(), "quality", "not_licensed")
    _refusal(write(), "quality", "not_licensed")
    _restore_pin(queue.workspace_id, original_pin)
    assert read().status_code == 200


def test_insights_module_states(client, email_outbox):
    def read(caller, ws):
        return caller.get(ws, "/api/v1/trends/issues")

    caller, user_id, workspace_id, _ = _check_module_states(
        client,
        email_outbox,
        email="cap-insights@example.com",
        module="insights",
        read=read,
        write=read,  # trends has only reads; the write probe is the same read
        counted=lambda: _rows_without_module_toggles(AuditEvent),
    )

    # the session-only views are guarded by the same module check
    assert caller.get(workspace_id, "/api/v1/trends/me").status_code == 200
    caller.put_module(workspace_id, "insights", False)
    _refusal(caller.get(workspace_id, "/api/v1/trends/me"), "insights", "disabled_by_admin")
    _refusal(caller.get(workspace_id, "/api/v1/trends/teams"), "insights", "disabled_by_admin")
    _refusal(
        caller.get(workspace_id, "/api/v1/trends/conversations"), "insights", "disabled_by_admin"
    )


def test_voice_module_states_and_the_operator_exception(voice_plane, email_outbox):
    client = voice_plane

    def read(caller, ws):
        return caller.get(ws, "/api/v1/voice/capacity")

    def write(caller, ws):
        return caller.request(
            ws,
            "POST",
            "/api/v1/voice/calls",
            json={
                "external_call_id": f"cap-{ws[:8]}",
                "direction": "inbound",
                "channel": "sip",
            },
        )

    def counted():
        from backend.voice.models import VoiceCallSession

        return _rows_without_module_toggles(VoiceCallSession, AuditEvent)

    # an operator of another workspace registers a healthy node, so the write
    # probe's normal result is 201
    operator, _, operator_ws = _caller(client, email_outbox, email="cap-voice-op@example.com")
    _make_operator("cap-voice-op@example.com")
    node = operator.request(
        operator_ws,
        "POST",
        "/api/v1/voice/nodes",
        json={"node_id": "cap-media", "max_concurrent": 4},
    )
    assert node.status_code == 200, node.text

    caller, user_id, workspace_id, email = _check_module_states(
        client,
        email_outbox,
        email="cap-voice@example.com",
        module="voice",
        read=read,
        write=write,
        counted=counted,
    )

    # the four platform-operator routes are install infrastructure, never guarded
    _make_operator(email)
    caller.put_module(workspace_id, "voice", False)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(client.app.state.settings, "disabled_modules", "voice")
        _refusal(read(caller, workspace_id), "voice", "disabled_for_install")
        heartbeat = caller.request(
            workspace_id, "POST", "/api/v1/voice/nodes/cap-media/heartbeat", json={"generation": 1}
        )
        assert heartbeat.status_code == 200, heartbeat.text
        drained = caller.request(
            workspace_id, "POST", "/api/v1/voice/nodes/cap-media/drain", json={"draining": False}
        )
        assert drained.status_code == 200, drained.text
        recovered = caller.request(workspace_id, "POST", "/api/v1/voice/recover")
        assert recovered.status_code == 200, recovered.text
        registered = caller.request(
            workspace_id,
            "POST",
            "/api/v1/voice/nodes",
            json={"node_id": "cap-media-2", "max_concurrent": 1},
        )
        assert registered.status_code == 200, registered.text


def test_voicemail_module_states_and_data_kept(vm_allowed, email_outbox):
    client = vm_allowed

    def read(caller, ws):
        return caller.get(ws, "/api/v1/voicemails")

    def write(caller, ws):
        return _import_vm(caller, ws, external_id=f"vm-{ws[:8]}")

    def counted():
        return _rows_without_module_toggles(VoicemailMessage, AuditEvent)

    caller, user_id, workspace_id, _ = _check_module_states(
        client,
        email_outbox,
        email="cap-voicemail@example.com",
        module="voicemail",
        read=read,
        write=write,
        counted=counted,
    )

    # data kept: the same voicemail comes back unchanged after off/on
    listed = caller.get(workspace_id, "/api/v1/voicemails").json()["items"]
    assert [item["external_message_id"] for item in listed] == [f"vm-{workspace_id[:8]}"]


def test_integrations_module_states(client, email_outbox):
    def read(caller, ws):
        return caller.get(ws, "/api/v1/integrations/mapping-profiles")

    def write(caller, ws):
        return caller.request(
            ws, "PUT", "/api/v1/integrations/mapping-profiles/odoo", json=_mapping_profile()
        )

    caller, user_id, workspace_id, _ = _check_module_states(
        client,
        email_outbox,
        email="cap-integrations@example.com",
        module="integrations",
        read=read,
        write=write,
        counted=lambda: _rows_without_module_toggles(IntegrationMappingProfile, AuditEvent),
    )
    # webhooks.py carries the same module guard
    caller.put_module(workspace_id, "integrations", False)
    for path in ("/api/v1/integrations/mapping-profiles", "/api/v1/webhooks/endpoints"):
        _refusal(caller.get(workspace_id, path), "integrations", "disabled_by_admin")


# --- 8. tenants, order of refusal, data isolation ----------------------------------------


def test_disable_is_per_workspace(vm_allowed, email_outbox):
    client = vm_allowed
    caller_a, user_a, ws_a = _caller(client, email_outbox, email="cap-tenant-a@example.com")
    caller_b, user_b, ws_b = _caller(client, email_outbox, email="cap-tenant-b@example.com")
    # the same user is a member of both workspaces
    _add_membership(workspace_id=ws_b, user_id=user_a, role=WorkspaceRole.ADMIN)

    assert _import_vm(caller_a, ws_a).status_code == 202
    assert caller_a.put_module(ws_a, "voicemail", False).status_code == 200

    _refusal(caller_a.get(ws_a, "/api/v1/voicemails"), "voicemail", "disabled_by_admin")
    _refusal(_import_vm(caller_a, ws_a, external_id="vm-refused"), "voicemail", "disabled_by_admin")
    # the same user, the other workspace: still enabled
    assert caller_a.get(ws_b, "/api/v1/voicemails").status_code == 200
    # a separate user in the other workspace: still enabled
    assert caller_b.get(ws_b, "/api/v1/voicemails").status_code == 200
    assert caller_a.put_module(ws_b, "voicemail", False).status_code == 200
    _refusal(caller_a.get(ws_b, "/api/v1/voicemails"), "voicemail", "disabled_by_admin")
    assert _stored_settings(ws_a)["modules"]["voicemail"]["enabled"] is False
    stored_b = _stored_settings(ws_b)["modules"]["voicemail"]
    assert stored_b["enabled"] is False and stored_b["changed_by"] == user_a


def test_order_of_refusal_and_role_independence(client, email_outbox):
    caller, _, workspace_id = _caller(client, email_outbox, email="cap-order@example.com")
    member = _caller(client, email_outbox, email="cap-order-member@example.com")
    _add_membership(workspace_id=workspace_id, user_id=member[1], role=WorkspaceRole.MEMBER)
    outsider = _caller(client, email_outbox, email="cap-order-outsider@example.com")

    caller.put_module(workspace_id, "quality", False)

    # no session -> the existing 401, never module_disabled
    client.cookies.clear()
    anonymous = client.get("/api/v1/scorecards", headers={"X-Workspace-ID": workspace_id})
    assert anonymous.status_code == 401 and _code(anonymous) == "authentication_required"

    # no membership -> the existing 403, never module_disabled
    refused = outsider[0].get(workspace_id, "/api/v1/scorecards")
    assert refused.status_code == 403 and _code(refused) == "workspace_access_denied"

    # the module check does not depend on the caller's role
    _refusal(member[0].get(workspace_id, "/api/v1/scorecards"), "quality", "disabled_by_admin")
    _refusal(caller.get(workspace_id, "/api/v1/scorecards"), "quality", "disabled_by_admin")

    # with the module enabled, a viewer is refused by the route's own policy instead
    caller.put_module(workspace_id, "quality", True)
    viewer = _caller(client, email_outbox, email="cap-order-viewer@example.com")
    _add_membership(workspace_id=workspace_id, user_id=viewer[1], role=WorkspaceRole.VIEWER)
    assert viewer[0].get(workspace_id, "/api/v1/scorecards").status_code == 200
    assert member[0].get(workspace_id, "/api/v1/scorecards").status_code == 200


def test_trends_api_key_order_in_every_module_state(client, email_outbox):
    SESSION_ONLY = ("/api/v1/trends/teams", "/api/v1/trends/me", "/api/v1/trends/conversations")
    caller, _, workspace_id = _caller(client, email_outbox, email="cap-trends-key@example.com")
    owner_headers = {"X-Workspace-ID": workspace_id, "X-CSRF-Token": caller.cookies[CSRF_COOKIE]}
    minted = _mint(
        client, owner_headers, caller.cookies, name="cap-trends", scopes=["meeting:read"]
    )
    key = minted.json()["key"]

    issues = client.get(
        "/api/v1/trends/issues", headers=_bearer(key, **{"X-Workspace-ID": workspace_id})
    )
    assert issues.status_code == 200, issues.text

    # while the module is enabled: every bearer that looks like a key gets the
    # one session-only answer, before any key lookup (valid and unknown alike)
    for path in SESSION_ONLY:
        with_key = client.get(path, headers=_bearer(key, **{"X-Workspace-ID": workspace_id}))
        assert with_key.status_code == 401 and _code(with_key) == "api_key_not_accepted", path
        unknown = client.get(
            path, headers=_bearer("hk_00000000.nope", **{"X-Workspace-ID": workspace_id})
        )
        assert unknown.status_code == 401 and _code(unknown) == "api_key_not_accepted", path

    # while the module is refused: the same answer, never module_disabled, so
    # the HOM-82 contract holds in every module state (amendment 2)
    caller.put_module(workspace_id, "insights", False)
    for path in SESSION_ONLY:
        refused = client.get(path, headers=_bearer(key, **{"X-Workspace-ID": workspace_id}))
        assert refused.status_code == 401 and _code(refused) == "api_key_not_accepted", path
        unknown = client.get(
            path, headers=_bearer("hk_00000000.nope", **{"X-Workspace-ID": workspace_id})
        )
        assert unknown.status_code == 401 and _code(unknown) == "api_key_not_accepted", path
    # the key-accepting route keeps the module refusal for the same key
    _refusal(
        client.get(
            "/api/v1/trends/issues", headers=_bearer(key, **{"X-Workspace-ID": workspace_id})
        ),
        "insights",
        "disabled_by_admin",
    )
    # a session caller on the same views still gets the module refusal, after
    # the workspace resolution and before the permission check
    _refusal(caller.get(workspace_id, "/api/v1/trends/me"), "insights", "disabled_by_admin")
    _refusal(caller.get(workspace_id, "/api/v1/trends/teams"), "insights", "disabled_by_admin")
    _refusal(
        caller.get(workspace_id, "/api/v1/trends/conversations"),
        "insights",
        "disabled_by_admin",
    )
