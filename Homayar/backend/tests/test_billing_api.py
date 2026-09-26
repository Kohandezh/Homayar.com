from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend.database.saas_models import (
    Membership,
    Plan,
    Subscription,
    UsageEvent,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.routers.v1.billing import router as billing_router
from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user


@pytest.fixture
def billing_client(client: TestClient) -> TestClient:
    client.app.include_router(billing_router, prefix="/api/v1")
    return client


def _session_cookies(client: TestClient) -> dict[str, str]:
    return {
        name: value
        for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
        if (value := client.cookies.get(name))
    }


def _workspace_for_user(db, user_id: str) -> Workspace:
    workspace = db.scalar(
        select(Workspace)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user_id)
        .order_by(Membership.created_at.asc())
    )
    assert workspace is not None
    return workspace


def _register_session(
    client: TestClient,
    email_outbox: list[dict[str, str]],
    *,
    email: str,
) -> tuple[dict[str, str], str, str]:
    credentials = register_user(client, email_outbox, email=email)
    login_user(client, credentials)
    cookies = _session_cookies(client)
    factory = inspection_session_factory()
    with factory() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        workspace = _workspace_for_user(db, user.id)
        return cookies, user.id, workspace.id


def test_billing_catalog_subscription_and_usage_are_tenant_scoped(
    billing_client: TestClient,
    email_outbox: list[dict[str, str]],
):
    owner_cookies, _owner_id, owner_workspace_id = _register_session(
        billing_client,
        email_outbox,
        email="billing-owner@example.com",
    )
    outsider_cookies, _outsider_id, outsider_workspace_id = _register_session(
        billing_client,
        email_outbox,
        email="billing-outsider@example.com",
    )

    now = datetime.now(UTC)
    factory = inspection_session_factory()
    with factory() as db:
        db.add_all(
            [
                UsageEvent(
                    workspace_id=owner_workspace_id,
                    operation_key="test:tenant:owner",
                    metric="audio_seconds",
                    value=120,
                    event_type="consumed",
                    subject_type="meeting",
                    subject_id="owner-meeting",
                    provider_metadata={"internal_cost": "must-not-leak"},
                    occurred_at=now,
                ),
                UsageEvent(
                    workspace_id=outsider_workspace_id,
                    operation_key="test:tenant:outsider",
                    metric="audio_seconds",
                    value=999,
                    event_type="consumed",
                    subject_type="meeting",
                    subject_id="outsider-meeting",
                    occurred_at=now,
                ),
            ]
        )
        db.commit()

    headers = {"X-Workspace-ID": owner_workspace_id}
    subscription = billing_client.get(
        "/api/v1/billing/subscription",
        cookies=owner_cookies,
        headers=headers,
    )
    assert subscription.status_code == 200, subscription.text
    assert subscription.json()["workspace_id"] == owner_workspace_id
    assert subscription.json()["plan"]["key"] == "demo"
    assert subscription.json()["subscription"]["status"] == "trialing"
    assert subscription.json()["price_published"] is False

    catalog = billing_client.get(
        "/api/v1/billing/plans",
        cookies=owner_cookies,
        headers=headers,
    )
    assert catalog.status_code == 200, catalog.text
    plan_keys = {item["key"] for item in catalog.json()["items"]}
    assert plan_keys == {"demo", "professional", "business"}
    assert all(item["price_published"] is False for item in catalog.json()["items"])
    assert all("price" not in item for item in catalog.json()["items"])

    usage = billing_client.get(
        "/api/v1/billing/usage",
        cookies=owner_cookies,
        headers=headers,
    )
    assert usage.status_code == 200, usage.text
    snapshots = {item["metric"]: item for item in usage.json()["items"]}
    assert snapshots["audio_seconds"]["remaining"] == 1800
    assert snapshots["concurrent_jobs"]["remaining"] == 1

    events = billing_client.get(
        "/api/v1/billing/usage/events",
        cookies=owner_cookies,
        headers=headers,
    )
    assert events.status_code == 200, events.text
    assert [item["subject_id"] for item in events.json()["items"]] == ["owner-meeting"]
    assert "provider_metadata" not in events.json()["items"][0]

    cross_tenant = billing_client.get(
        "/api/v1/billing/usage/events",
        cookies=owner_cookies,
        headers={"X-Workspace-ID": outsider_workspace_id},
    )
    assert cross_tenant.status_code == 403
    assert cross_tenant.json()["error"]["code"] == "workspace_access_denied"

    outsider_own = billing_client.get(
        "/api/v1/billing/usage/events",
        cookies=outsider_cookies,
        headers={"X-Workspace-ID": outsider_workspace_id},
    )
    assert outsider_own.status_code == 200
    assert [item["subject_id"] for item in outsider_own.json()["items"]] == ["outsider-meeting"]


def test_billing_rbac_and_manual_management_are_explicitly_read_only(
    billing_client: TestClient,
    email_outbox: list[dict[str, str]],
):
    owner_cookies, _owner_id, workspace_id = _register_session(
        billing_client,
        email_outbox,
        email="billing-rbac-owner@example.com",
    )
    admin_cookies, admin_id, _admin_workspace_id = _register_session(
        billing_client,
        email_outbox,
        email="billing-rbac-admin@example.com",
    )

    factory = inspection_session_factory()
    with factory() as db:
        db.add(
            Membership(
                workspace_id=workspace_id,
                user_id=admin_id,
                role=WorkspaceRole.ADMIN,
            )
        )
        db.commit()

    headers = {"X-Workspace-ID": workspace_id}
    admin_read = billing_client.get(
        "/api/v1/billing/subscription",
        cookies=admin_cookies,
        headers=headers,
    )
    assert admin_read.status_code == 200

    admin_management = billing_client.get(
        "/api/v1/billing/management",
        cookies=admin_cookies,
        headers=headers,
    )
    assert admin_management.status_code == 403
    assert admin_management.json()["error"]["code"] == "permission_denied"

    with factory() as db:
        membership = db.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == admin_id,
            )
        )
        assert membership is not None
        membership.role = WorkspaceRole.MEMBER
        db.commit()

    member_read = billing_client.get(
        "/api/v1/billing/usage",
        cookies=admin_cookies,
        headers=headers,
    )
    assert member_read.status_code == 403
    assert member_read.json()["error"]["code"] == "permission_denied"

    owner_management = billing_client.get(
        "/api/v1/billing/management",
        cookies=owner_cookies,
        headers=headers,
    )
    assert owner_management.status_code == 200, owner_management.text
    assert owner_management.json() == {
        "workspace_id": workspace_id,
        "mode": "manual_read_only",
        "write_operations_enabled": False,
        "contact_request_supported": False,
        "mutation_endpoints": [],
        "message": (
            "Billing is managed manually; this API does not persist plan changes or contact "
            "requests."
        ),
    }

    unsupported_write = billing_client.post(
        "/api/v1/billing/management",
        cookies=owner_cookies,
        headers=headers,
        json={"plan_key": "professional"},
    )
    assert unsupported_write.status_code == 405
    assert unsupported_write.json() == {"detail": "Method Not Allowed"}


def test_usage_event_cursor_is_stable_and_errors_are_standardized(
    billing_client: TestClient,
    email_outbox: list[dict[str, str]],
):
    cookies, _user_id, workspace_id = _register_session(
        billing_client,
        email_outbox,
        email="billing-events@example.com",
    )
    occurred_at = datetime.now(UTC) - timedelta(minutes=1)
    factory = inspection_session_factory()
    with factory() as db:
        db.add_all(
            [
                UsageEvent(
                    id=f"00000000-0000-0000-0000-00000000000{index}",
                    workspace_id=workspace_id,
                    operation_key=f"test:cursor:{index}",
                    metric="audio_seconds",
                    value=index,
                    event_type="consumed",
                    subject_type="meeting",
                    subject_id=f"meeting-{index}",
                    occurred_at=occurred_at,
                )
                for index in range(1, 4)
            ]
        )
        db.commit()

    headers = {"X-Workspace-ID": workspace_id}
    first = billing_client.get(
        "/api/v1/billing/usage/events?limit=2&metric=audio_seconds",
        cookies=cookies,
        headers=headers,
    )
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert [item["subject_id"] for item in first_payload["items"]] == [
        "meeting-3",
        "meeting-2",
    ]
    assert first_payload["next_before"] is not None
    assert first_payload["next_before_id"].endswith("2")

    second = billing_client.get(
        "/api/v1/billing/usage/events",
        params={
            "limit": 2,
            "before": first_payload["next_before"],
            "before_id": first_payload["next_before_id"],
        },
        cookies=cookies,
        headers=headers,
    )
    assert second.status_code == 200, second.text
    assert [item["subject_id"] for item in second.json()["items"]] == ["meeting-1"]
    assert second.json()["next_before"] is None
    assert second.json()["next_before_id"] is None

    invalid_cursor = billing_client.get(
        "/api/v1/billing/usage/events?before_id=cursor-without-time",
        cookies=cookies,
        headers=headers,
    )
    assert invalid_cursor.status_code == 400
    assert invalid_cursor.json()["error"]["code"] == "invalid_usage_cursor"

    billing_client.cookies.clear()
    unauthenticated = billing_client.get(
        "/api/v1/billing/subscription",
        headers=headers,
    )
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "authentication_required"


def test_billing_router_has_no_mutation_surface():
    methods = {
        method for route in billing_router.routes for method in getattr(route, "methods", set())
    }
    assert methods == {"GET"}


def test_current_subscription_can_be_empty_without_fabricating_a_plan(
    billing_client: TestClient,
    email_outbox: list[dict[str, str]],
):
    cookies, _user_id, workspace_id = _register_session(
        billing_client,
        email_outbox,
        email="billing-no-subscription@example.com",
    )
    factory = inspection_session_factory()
    with factory() as db:
        db.execute(delete(Subscription).where(Subscription.workspace_id == workspace_id))
        demo_plan = db.get(Plan, "demo")
        assert demo_plan is not None
        demo_plan.is_active = False
        db.commit()

    subscription = billing_client.get(
        "/api/v1/billing/subscription",
        cookies=cookies,
        headers={"X-Workspace-ID": workspace_id},
    )
    assert subscription.status_code == 200
    assert subscription.json()["plan"] is None
    assert subscription.json()["subscription"] is None

    response = billing_client.get(
        "/api/v1/billing/plans",
        cookies=cookies,
        headers={"X-Workspace-ID": workspace_id},
    )
    assert response.status_code == 200
    assert "demo" not in {item["key"] for item in response.json()["items"]}
