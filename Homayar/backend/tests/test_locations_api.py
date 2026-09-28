from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select

import backend.locations.models  # noqa: F401 - registers service_locations on Base
from backend.config import Settings, get_settings
from backend.database.relational import (
    configure_database,
    create_schema_for_test_or_development,
    reset_database_state,
)
from backend.database.saas_models import AuditEvent, Membership, User, WorkspaceRole
from backend.routers.v1 import auth as auth_router
from backend.routers.v1 import locations as locations_router
from backend.security.context import CSRF_COOKIE
from backend.services.rate_limit_service import RateLimiter
from backend.services.tenancy_service import ensure_plan_catalog
from backend.tests._database import TEST_APP_ROLE, inspection_session_factory
from backend.tests._database import test_database_url as _database_url
from backend.tests._transport import APP_REDIS_URL


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        deployment_mode="cloud",
        database_url=_database_url(tmp_path, name="locations"),
        database_app_role=TEST_APP_ROLE,
        redis_url=APP_REDIS_URL,
        jwt_secret="j" * 48,
        token_pepper="p" * 48,
        auto_create_schema=True,
        tasks_eager=True,
        secure_cookies=False,
        enable_google_auth=False,
        trusted_hosts="testserver,localhost,127.0.0.1",
        frontend_url="http://testserver/app",
        public_url="http://testserver",
        _env_file=None,
    )


@pytest.fixture
def email_outbox() -> list[dict[str, str]]:
    return []


@pytest.fixture
def client(settings, email_outbox, monkeypatch) -> Generator[TestClient, None, None]:
    async def capture_verification(user, token, _settings):
        email_outbox.append({"kind": "verify", "user_id": user.id, "token": token})

    async def capture_reset(user, token, _settings):
        email_outbox.append({"kind": "reset", "user_id": user.id, "token": token})

    monkeypatch.setattr(auth_router, "_send_verification_message", capture_verification)
    monkeypatch.setattr(auth_router, "_send_reset_message", capture_reset)
    RateLimiter.reset_memory()
    configure_database(settings)
    create_schema_for_test_or_development(settings)
    with inspection_session_factory()() as db:
        ensure_plan_catalog(db, settings)
        db.commit()

    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: settings
    app.include_router(auth_router.router, prefix=settings.api_prefix)
    app.include_router(locations_router.router, prefix=settings.api_prefix)

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={"error": {"code": str(detail.get("code", "http_error")), "detail": detail}},
        )

    with TestClient(app) as test_client:
        yield test_client
    reset_database_state()


def _login(client, email_outbox, email: str) -> dict:
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "SecurePass123",
            "display_name": email.split("@")[0],
            "locale": "fa",
        },
    )
    assert response.status_code == 202, response.text
    verified = client.post("/api/v1/auth/verify-email", json={"token": email_outbox[-1]["token"]})
    assert verified.status_code == 200, verified.text
    login = client.post(
        "/api/v1/auth/login", json={"identifier": email, "password": "SecurePass123"}
    )
    assert login.status_code == 200, login.text
    return {
        "workspace_id": login.json()["workspaces"][0]["id"],
        "user_id": login.json()["user"]["id"] if "user" in login.json() else None,
        "cookies": dict(client.cookies),
    }


def _use(client, session) -> None:
    client.cookies.clear()
    for name, value in session["cookies"].items():
        client.cookies.set(name, value)


def _headers(session, workspace_id: str | None = None) -> dict[str, str]:
    return {
        "X-Workspace-ID": workspace_id or session["workspace_id"],
        "X-CSRF-Token": session["cookies"][CSRF_COOKIE],
    }


BRANCHES = [
    {
        "code": "THR-1",
        "name": "شعبه ونک",
        "city": "تهران",
        "province": "تهران",
        "latitude": 35.7575,
        "longitude": 51.41,
        "service_tags": ["repair"],
        "postal_code": "14356-78913",
    },
    {
        "code": "KRJ-1",
        "name": "Karaj dealer",
        "kind": "dealer",
        "city": "کرج",
        "latitude": 35.84,
        "longitude": 50.9391,
    },
    {
        "code": "ISF-1",
        "name": "Isfahan centre",
        "kind": "service_center",
        "city": "اصفهان",
        "latitude": 32.6546,
        "longitude": 51.668,
        "opening_hours": {"sat": [["09:00", "17:00"]]},
    },
]


def test_crud_nearest_resolve_and_audit(client, email_outbox):
    owner = _login(client, email_outbox, "loc-owner@example.com")
    _use(client, owner)
    ids = {}
    for body in BRANCHES:
        response = client.post("/api/v1/locations", json=body, headers=_headers(owner))
        assert response.status_code == 201, response.text
        ids[body["code"]] = response.json()["id"]
    created = client.get(f"/api/v1/locations/{ids['THR-1']}", headers=_headers(owner)).json()
    assert created["postal_code"] == "1435678913"
    assert created["workspace_id"] == owner["workspace_id"]

    duplicate = client.post("/api/v1/locations", json=BRANCHES[0], headers=_headers(owner))
    assert duplicate.status_code == 409

    bad = client.post(
        "/api/v1/locations",
        json={"code": "BAD", "name": "bad", "postal_code": "0000000000"},
        headers=_headers(owner),
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_address"

    abroad = client.post(
        "/api/v1/locations",
        json={"code": "PAR", "name": "Paris", "latitude": 48.85, "longitude": 2.35},
        headers=_headers(owner),
    )
    assert abroad.status_code == 201
    assert "coordinates_outside_iran" in abroad.json()["warnings"]

    listing = client.get("/api/v1/locations?city=تهران", headers=_headers(owner)).json()
    assert [i["code"] for i in listing["items"]] == ["THR-1"]

    nearest = client.post(
        "/api/v1/locations/nearest",
        json={"text": "سلام، از كرج زنگ می‌زنم", "limit": 2, "max_distance_km": 100},
        headers=_headers(owner),
    )
    assert nearest.status_code == 200, nearest.text
    body = nearest.json()
    assert body["status"] == "ok"
    assert body["query"]["precision"] == "city"
    assert [r["location"]["code"] for r in body["results"]] == ["KRJ-1", "THR-1"]
    assert body["results"][0]["distance_km"] < 5

    by_coords = client.post(
        "/api/v1/locations/nearest",
        json={"latitude": 32.66, "longitude": 51.67, "kinds": ["service_center"]},
        headers=_headers(owner),
    ).json()
    assert [r["location"]["code"] for r in by_coords["results"]] == ["ISF-1"]

    ambiguous = client.post(
        "/api/v1/locations/nearest", json={"text": "از نورآباد هستم"}, headers=_headers(owner)
    ).json()
    assert ambiguous["status"] == "needs_clarification"
    assert ambiguous["results"] == []
    assert len(ambiguous["clarification"]["options"]) == 2

    resolved = client.post(
        "/api/v1/locations/resolve",
        json={"text": "استان فارس، شهر شیراز، خیابان زند، پلاک ۱۰"},
        headers=_headers(owner),
    ).json()
    assert resolved["status"] == "resolved"
    assert resolved["resolved"]["city_key"] == "shiraz"
    assert "زند" in resolved["landmarks"]

    patched = client.patch(
        f"/api/v1/locations/{ids['KRJ-1']}", json={"is_active": False}, headers=_headers(owner)
    )
    assert patched.status_code == 200 and patched.json()["is_active"] is False
    after = client.post(
        "/api/v1/locations/nearest", json={"text": "کرج", "limit": 1}, headers=_headers(owner)
    ).json()
    assert after["results"][0]["location"]["code"] == "THR-1"

    deleted = client.delete(f"/api/v1/locations/{ids['ISF-1']}", headers=_headers(owner))
    assert deleted.status_code == 204
    missing = client.get(f"/api/v1/locations/{ids['ISF-1']}", headers=_headers(owner))
    assert missing.status_code == 404

    with inspection_session_factory()() as db:
        events = db.scalars(
            select(AuditEvent.event_type).where(AuditEvent.target_type == "service_location")
        ).all()
    assert events.count("service_location.created") == 4
    assert "service_location.updated" in events and "service_location.deleted" in events


def test_cross_tenant_isolation_and_permission_negatives(client, email_outbox):
    owner = _login(client, email_outbox, "loc-a@example.com")
    other = _login(client, email_outbox, "loc-b@example.com")
    viewer = _login(client, email_outbox, "loc-viewer@example.com")

    with inspection_session_factory()() as db:
        viewer_user = db.scalar(select(User).where(User.email == "loc-viewer@example.com"))
        db.add(
            Membership(
                workspace_id=owner["workspace_id"],
                user_id=viewer_user.id,
                role=WorkspaceRole.VIEWER,
            )
        )
        db.commit()

    _use(client, owner)
    created = client.post("/api/v1/locations", json=BRANCHES[0], headers=_headers(owner))
    assert created.status_code == 201
    location_id = created.json()["id"]

    # Another tenant cannot see, list, route to, patch or delete it.
    _use(client, other)
    assert (
        client.get(f"/api/v1/locations/{location_id}", headers=_headers(other)).status_code == 404
    )
    assert client.get("/api/v1/locations", headers=_headers(other)).json()["total"] == 0
    routed = client.post(
        "/api/v1/locations/nearest", json={"text": "تهران"}, headers=_headers(other)
    ).json()
    assert routed["status"] == "no_match"
    assert (
        client.patch(
            f"/api/v1/locations/{location_id}", json={"name": "x"}, headers=_headers(other)
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/v1/locations/{location_id}", headers=_headers(other)).status_code
        == 404
    )
    # Naming the owner's workspace in the header confers nothing.
    forged = client.get("/api/v1/locations", headers=_headers(other, owner["workspace_id"]))
    assert forged.status_code in {403, 404}

    # A viewer can read and route but not administer branches.
    _use(client, viewer)
    vh = _headers(viewer, owner["workspace_id"])
    assert client.get("/api/v1/locations", headers=vh).json()["total"] == 1
    assert (
        client.post("/api/v1/locations/nearest", json={"text": "تهران"}, headers=vh).json()[
            "status"
        ]
        == "ok"
    )
    assert client.post("/api/v1/locations", json={**BRANCHES[1]}, headers=vh).status_code == 403
    assert (
        client.patch(f"/api/v1/locations/{location_id}", json={"name": "x"}, headers=vh).status_code
        == 403
    )
    assert client.delete(f"/api/v1/locations/{location_id}", headers=vh).status_code == 403

    # Unauthenticated.
    client.cookies.clear()
    assert (
        client.get(
            "/api/v1/locations", headers={"X-Workspace-ID": owner["workspace_id"]}
        ).status_code
        == 401
    )


def test_write_requires_csrf(client, email_outbox):
    owner = _login(client, email_outbox, "loc-csrf@example.com")
    _use(client, owner)
    response = client.post(
        "/api/v1/locations", json=BRANCHES[0], headers={"X-Workspace-ID": owner["workspace_id"]}
    )
    assert response.status_code in {401, 403}


def test_nearest_request_validation(client, email_outbox):
    owner = _login(client, email_outbox, "loc-val@example.com")
    _use(client, owner)
    assert (
        client.post("/api/v1/locations/nearest", json={}, headers=_headers(owner)).status_code
        == 422
    )
    half = client.post(
        "/api/v1/locations/nearest", json={"latitude": 35.0}, headers=_headers(owner)
    )
    assert half.status_code == 422
    assert half.json()["error"]["code"] == "invalid_coordinates"
