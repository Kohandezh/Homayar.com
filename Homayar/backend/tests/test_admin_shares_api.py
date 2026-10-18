"""HOM-192: the workspace administrator's list of share links.

``GET /admin/shares`` pages every share link of the workspace newest first,
with ``status`` (active, revoked, expired) and ``audience`` filters. It needs
``workspace:manage``, which no API key can carry, is bound to the caller's
workspace and leaves out shares of soft-deleted meetings. It never returns
the token, its hash, a share URL or a recipient address. Shares are created
through ``POST /meetings/{meeting_id}/shares``; only their times are moved
with DBA writes, so status and order can be set exactly.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import event, func, select, update

from backend.database.relational import get_engine
from backend.database.saas_models import AuditEvent, Meeting, ShareAudience, ShareLink
from backend.jobs.leases import as_utc
from backend.security.authorization import API_KEY_SCOPES
from backend.security.context import CSRF_COOKIE
from backend.services import admin_shares_service
from backend.tests._database import inspection_session_factory
from backend.tests.test_admin_api import _register_and_login
from backend.tests.test_admin_jobs_api import _seed_job
from backend.tests.test_authorization_matrix import _insert_meeting, _workspace_with_roles

LIST = "/api/v1/admin/shares"
BASE_TIME = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
ITEM_KEYS = {
    "id",
    "meeting_id",
    "meeting_title",
    "audience",
    "status",
    "created_by_user_id",
    "created_at",
    "expires_at",
    "access_count",
    "last_accessed_at",
    "revoked_at",
}
STATUS_KEYS = {"active", "revoked", "expired"}
AUDIENCE_KEYS = {audience.value for audience in ShareAudience}


# --- helpers -----------------------------------------------------------------


class _Owner:
    """A signed-in workspace owner; ``headers`` carry the CSRF token for writes."""

    def __init__(self, client, email_outbox, email: str):
        self.client = client
        self.user_id, self.workspace_id, self.cookies = _register_and_login(
            client, email_outbox, email=email
        )
        self.headers = {
            "X-Workspace-ID": self.workspace_id,
            "X-CSRF-Token": self.cookies[CSRF_COOKIE],
        }

    def get(self, path: str = LIST, **params):
        return self.client.get(path, params=params, cookies=self.cookies, headers=self.headers)

    def meeting(self, title: str = "Board review") -> str:
        return _insert_meeting(self.workspace_id, created_by=self.user_id, title=title)

    def share(self, meeting_id: str, **payload) -> dict:
        created = self.client.post(
            f"/api/v1/meetings/{meeting_id}/shares",
            json=payload,
            cookies=self.cookies,
            headers=self.headers,
        )
        assert created.status_code == 201, created.text
        return created.json()

    def revoke(self, meeting_id: str, share_id: str) -> None:
        revoked = self.client.delete(
            f"/api/v1/meetings/{meeting_id}/shares/{share_id}",
            cookies=self.cookies,
            headers=self.headers,
        )
        assert revoked.status_code == 204, revoked.text

    def allow_public_links(self) -> None:
        patched = self.client.patch(
            "/api/v1/admin/workspace-settings",
            json={"public_share_links_enabled": True},
            cookies=self.cookies,
            headers=self.headers,
        )
        assert patched.status_code == 200, patched.text


def _set_share(share_id: str, **values) -> None:
    """Move a share's times (DBA write)."""
    with inspection_session_factory()() as db:
        db.execute(update(ShareLink).where(ShareLink.id == share_id).values(**values))
        db.commit()


def _soft_delete_meeting(meeting_id: str) -> None:
    with inspection_session_factory()() as db:
        db.execute(
            update(Meeting).where(Meeting.id == meeting_id).values(deleted_at=datetime.now(UTC))
        )
        db.commit()


def _ids(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def _walk(owner: _Owner, **params) -> list[str]:
    """Follow ``next_cursor`` to the last page; return every id in order."""
    ids: list[str] = []
    cursor: str | None = None
    for _ in range(50):
        query = dict(params)
        if cursor is not None:
            query["cursor"] = cursor
        response = owner.get(**query)
        assert response.status_code == 200, response.text
        body = response.json()
        ids.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return ids
        assert isinstance(cursor, str) and body["items"]
    raise AssertionError("paging did not reach the last page")


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def _instant(value: str) -> datetime:
    """A response timestamp as UTC (the SQLite fallback returns naive values)."""
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _encoded(payload: object) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decoded(cursor: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))


def _states(owner: _Owner) -> dict[str, str]:
    """``{share_id: status}`` for one page of everything."""
    response = owner.get(limit=100)
    assert response.status_code == 200, response.text
    return {item["id"]: item["status"] for item in response.json()["items"]}


# --- the list ----------------------------------------------------------------


def test_list_returns_exact_fields_newest_first(client, email_outbox):
    owner = _Owner(client, email_outbox, "shares-fields@example.com")
    meeting_id = owner.meeting("Quarterly board review")
    older = owner.share(meeting_id)
    newer = owner.share(meeting_id, expires_in_hours=None)
    _set_share(older["id"], created_at=BASE_TIME)
    _set_share(newer["id"], created_at=BASE_TIME + timedelta(minutes=1))

    response = owner.get()
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor"}
    assert body["next_cursor"] is None
    assert [item["id"] for item in body["items"]] == [newer["id"], older["id"]]
    for item in body["items"]:
        assert set(item) == ITEM_KEYS
        assert item["meeting_id"] == meeting_id
        assert item["meeting_title"] == "Quarterly board review"
        assert item["audience"] == "workspace"
        assert item["status"] == "active"
        assert item["created_by_user_id"] == owner.user_id
        assert item["access_count"] == 0
        assert item["last_accessed_at"] is None
        assert item["revoked_at"] is None
    first, second = body["items"]
    assert first["expires_at"] is None
    assert _instant(first["created_at"]) == BASE_TIME + timedelta(minutes=1)
    assert _instant(second["expires_at"]) == _instant(older["expires_at"])
    assert _instant(second["created_at"]) == BASE_TIME


def test_an_empty_workspace_lists_nothing(client, email_outbox):
    owner = _Owner(client, email_outbox, "shares-empty@example.com")
    response = owner.get()
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "next_cursor": None}


def test_the_list_never_returns_the_token_its_hash_a_url_or_recipients(client, email_outbox):
    owner = _Owner(client, email_outbox, "shares-secrets@example.com")
    owner.allow_public_links()
    meeting_id = owner.meeting()
    created = [
        owner.share(meeting_id, audience="link"),
        owner.share(meeting_id, audience="workspace"),
        owner.share(meeting_id, audience="organization"),
        owner.share(
            meeting_id,
            audience="recipients",
            recipients=["Outside.Reader@Example.com"],
            phone_recipients=["0912 123 0001"],
        ),
    ]
    owner.revoke(meeting_id, created[0]["id"])
    tokens = [share["url"].rsplit("/share/", 1)[1] for share in created]
    with inspection_session_factory()() as db:
        hashes = list(
            db.scalars(
                select(ShareLink.token_hash).where(
                    ShareLink.id.in_([share["id"] for share in created])
                )
            )
        )
    assert len(hashes) == 4 and all(len(token) >= 32 for token in tokens)
    forbidden = [
        *tokens,
        *hashes,
        *(share["url"] for share in created),
        "/share/",
        "outside.reader@example.com",
        "outside.reader",
        "+989121230001",
        "9121230001",
    ]

    for params in ({}, {"status": "active"}, {"status": "revoked"}, {"audience": "recipients"}):
        response = owner.get(limit=100, **params)
        assert response.status_code == 200, response.text
        assert response.json()["items"], params  # allow-control: a real, non-empty body
        lowered = response.text.lower()
        for value in forbidden:
            assert value.lower() not in lowered, (params, value)
        for item in response.json()["items"]:
            assert set(item) == ITEM_KEYS
    assert set(_ids(owner.get(limit=100))) == {share["id"] for share in created}


# --- filters -----------------------------------------------------------------


def test_status_filter_returns_exactly_the_links_in_that_state(client, email_outbox):
    owner = _Owner(client, email_outbox, "shares-status@example.com")
    meeting_id = owner.meeting()
    past = datetime.now(UTC) - timedelta(hours=1)
    active = owner.share(meeting_id)["id"]
    never_expires = owner.share(meeting_id, expires_in_hours=None)["id"]
    revoked = owner.share(meeting_id)["id"]
    owner.revoke(meeting_id, revoked)
    expired = owner.share(meeting_id, expires_in_hours=1)["id"]
    _set_share(expired, expires_at=past)
    revoked_and_expired = owner.share(meeting_id, expires_in_hours=1)["id"]
    owner.revoke(meeting_id, revoked_and_expired)
    _set_share(revoked_and_expired, expires_at=past)

    expected = {
        "active": {active, never_expires},
        "revoked": {revoked, revoked_and_expired},
        "expired": {expired},
    }
    for status, ids in expected.items():
        response = owner.get(status=status)
        assert set(_ids(response)) == ids, status
        assert {item["status"] for item in response.json()["items"]} == {status}
    assert _states(owner) == {
        active: "active",
        never_expires: "active",
        revoked: "revoked",
        revoked_and_expired: "revoked",
        expired: "expired",
    }


def test_a_link_is_expired_at_its_exact_expiry_instant(client, email_outbox, monkeypatch):
    owner = _Owner(client, email_outbox, "shares-expiry-instant@example.com")
    share_id = owner.share(owner.meeting(), expires_in_hours=1)["id"]
    with inspection_session_factory()() as db:
        expires_at = as_utc(db.scalar(select(ShareLink.expires_at).where(ShareLink.id == share_id)))
    assert expires_at is not None

    def database_clock_at(instant: datetime) -> None:
        monkeypatch.setattr(admin_shares_service, "database_now", lambda _db: instant)

    # the same "<=" rule as the public access check
    database_clock_at(expires_at)
    assert _states(owner) == {share_id: "expired"}
    assert _ids(owner.get(status="expired")) == [share_id]
    assert _ids(owner.get(status="active")) == []

    # allow-control: one microsecond earlier the link is still active
    database_clock_at(expires_at - timedelta(microseconds=1))
    assert _states(owner) == {share_id: "active"}
    assert _ids(owner.get(status="active")) == [share_id]
    assert _ids(owner.get(status="expired")) == []


def test_audience_filter_combines_with_status(client, email_outbox):
    owner = _Owner(client, email_outbox, "shares-audience@example.com")
    owner.allow_public_links()
    meeting_id = owner.meeting()
    link_active = owner.share(meeting_id, audience="link")["id"]
    link_revoked = owner.share(meeting_id, audience="link")["id"]
    owner.revoke(meeting_id, link_revoked)
    workspace_active = owner.share(meeting_id, audience="workspace")["id"]
    organization_active = owner.share(meeting_id, audience="organization")["id"]
    recipients_active = owner.share(
        meeting_id, audience="recipients", recipients=["reader@example.com"]
    )["id"]

    cases = [
        ({"audience": "link"}, {link_active, link_revoked}),
        ({"audience": "link", "status": "active"}, {link_active}),
        ({"audience": "link", "status": "revoked"}, {link_revoked}),
        ({"audience": "workspace"}, {workspace_active}),
        ({"audience": "workspace", "status": "revoked"}, set()),
        ({"audience": "organization"}, {organization_active}),
        ({"audience": "recipients", "status": "active"}, {recipients_active}),
        ({"audience": "recipients", "status": "expired"}, set()),
        (
            {"status": "active"},
            {link_active, workspace_active, organization_active, recipients_active},
        ),
    ]
    for params, ids in cases:
        response = owner.get(**params)
        assert set(_ids(response)) == ids, params
        for item in response.json()["items"]:
            for key, value in params.items():
                assert item[key] == value, (params, item)


# --- paging and validation ---------------------------------------------------


def test_paging_returns_every_link_once_including_created_at_ties(client, email_outbox):
    owner = _Owner(client, email_outbox, "shares-paging@example.com")
    meeting_id = owner.meeting()
    # three links share a whole-second instant, two share a sub-second one, and
    # two are 1 microsecond apart: a cursor cut to whole seconds loses links
    offsets = (
        timedelta(0),
        timedelta(0),
        timedelta(0),
        timedelta(minutes=1, microseconds=250_000),
        timedelta(minutes=1, microseconds=250_000),
        timedelta(minutes=2, microseconds=500_000),
        timedelta(minutes=2, microseconds=500_001),
        timedelta(minutes=3),
    )
    created_at: dict[str, datetime] = {}
    for offset in offsets:
        share_id = owner.share(meeting_id)["id"]
        created_at[share_id] = BASE_TIME + offset
        _set_share(share_id, created_at=created_at[share_id])
    revoked = sorted(created_at)[:3]
    for share_id in revoked:
        owner.revoke(meeting_id, share_id)
    expected = sorted(created_at, key=lambda share_id: (created_at[share_id], share_id))[::-1]

    assert _ids(owner.get(limit=100)) == expected
    for limit in (1, 2, 3):
        walked = _walk(owner, limit=limit)
        assert walked == expected, limit
        assert len(set(walked)) == len(walked)
    walked_revoked = _walk(owner, limit=1, status="revoked")
    assert walked_revoked == [share_id for share_id in expected if share_id in revoked]

    last = owner.get(limit=len(expected))
    assert _ids(last) == expected and last.json()["next_cursor"] is None


def test_invalid_cursors_filters_and_limits_are_refused(client, email_outbox):
    owner = _Owner(client, email_outbox, "shares-cursor@example.com")
    meeting_id = owner.meeting()
    first = owner.share(meeting_id)["id"]
    second = owner.share(meeting_id)["id"]
    _set_share(first, created_at=BASE_TIME + timedelta(minutes=1))
    _set_share(second, created_at=BASE_TIME)

    # real cursors of the two other administrator lists
    for minute in range(2):
        _seed_job(owner.workspace_id, created_at=BASE_TIME + timedelta(minutes=minute))
    jobs_cursor = owner.get("/api/v1/admin/jobs", limit=1).json()["next_cursor"]
    audit_cursor = owner.get("/api/v1/admin/audit-events", limit=1).json()["next_cursor"]
    assert isinstance(jobs_cursor, str) and isinstance(audit_cursor, str)

    stamp = BASE_TIME.isoformat()
    bad_cursors = [
        "not-a-cursor!!",
        "AAAA",
        _encoded(["v", 1]),
        jobs_cursor,
        audit_cursor,
        _encoded({"v": 1, "created_at": stamp, "id": first}),
        _encoded({"v": 1, "occurred_at": stamp, "id": first}),
    ]
    page = owner.get(limit=1)
    share_cursor = page.json()["next_cursor"]
    good = json.loads(base64.urlsafe_b64decode(share_cursor + "=" * (-len(share_cursor) % 4)))
    # its own keys, so a jobs or audit-events cursor never decodes as a share cursor
    assert set(good) == {"v", "share_created_at", "share_id"}
    bad_cursors += [
        _encoded({**good, "v": 2}),
        _encoded({**good, "share_id": "not-a-uuid"}),
        _encoded({**good, "share_created_at": "2026-01-01T09:00:00"}),
        _encoded({**good, "share_created_at": "yesterday"}),
        _encoded({**good, "share_created_at": 1}),
        _encoded({**good, "extra": 1}),
    ]
    for cursor in bad_cursors:
        refused = owner.get(cursor=cursor)
        assert refused.status_code == 400, (cursor, refused.text)
        assert _error_code(refused) == "invalid_cursor"

    # a share cursor is not a cursor of the other lists either
    for path in ("/api/v1/admin/jobs", "/api/v1/admin/audit-events"):
        refused = owner.get(path, cursor=share_cursor)
        assert refused.status_code == 400, (path, refused.text)
        assert _error_code(refused) == "invalid_cursor"

    for cursor in ("", "a" * 513):
        assert owner.get(cursor=cursor).status_code == 422
    invalid_params = [
        {"limit": 0},
        {"limit": 101},
        {"limit": -1},
        {"limit": "many"},
        {"status": "deleted"},
        {"status": "ACTIVE"},
        {"audience": "public"},
        {"audience": "LINK"},
    ]
    for params in invalid_params:
        refused = owner.get(**params)
        assert refused.status_code == 422, (params, refused.text)
        assert _error_code(refused) == "validation_error"
    for limit in (1, 100):
        assert owner.get(limit=limit).status_code == 200

    # allow-control: the real next_cursor gives the next page
    assert _ids(page) == [first]
    following = owner.get(limit=1, cursor=share_cursor)
    assert _ids(following) == [second]
    assert following.json()["next_cursor"] is None


def test_the_cursor_version_must_be_the_integer_one(client, email_outbox):
    owner = _Owner(client, email_outbox, "shares-cursor-version@example.com")
    meeting_id = owner.meeting()
    first = owner.share(meeting_id)["id"]
    second = owner.share(meeting_id)["id"]
    _set_share(first, created_at=BASE_TIME + timedelta(minutes=1))
    _set_share(second, created_at=BASE_TIME)
    good = _decoded(owner.get(limit=1).json()["next_cursor"])

    # JSON true equals 1 in Python, so the type is checked as well
    for version in (True, 1.0, "1", None):
        refused = owner.get(cursor=_encoded({**good, "v": version}))
        assert refused.status_code == 400, (version, refused.text)
        assert _error_code(refused) == "invalid_cursor"

    # allow-control: the same payload with the integer 1
    assert _ids(owner.get(limit=1, cursor=_encoded({**good, "v": 1}))) == [second]


def test_a_cursor_in_another_utc_offset_gives_the_same_page(client, email_outbox):
    owner = _Owner(client, email_outbox, "shares-cursor-offset@example.com")
    meeting_id = owner.meeting()
    for minute in range(4):
        share_id = owner.share(meeting_id)["id"]
        _set_share(share_id, created_at=BASE_TIME + timedelta(minutes=minute, microseconds=123_456))
    cursor = owner.get(limit=1).json()["next_cursor"]
    good = _decoded(cursor)
    instant = datetime.fromisoformat(good["share_created_at"])
    assert instant.utcoffset() == timedelta(0)
    page = owner.get(limit=2, cursor=cursor)
    expected = _ids(page)
    assert len(expected) == 2

    for offset in (timedelta(hours=3, minutes=30), timedelta(hours=-5)):
        shifted = instant.astimezone(timezone(offset)).isoformat()
        assert shifted != good["share_created_at"]
        moved = owner.get(limit=2, cursor=_encoded({**good, "share_created_at": shifted}))
        assert _ids(moved) == expected, offset
        assert moved.json()["next_cursor"] == page.json()["next_cursor"], offset


def test_a_cursor_time_that_overflows_in_utc_is_refused(client, email_outbox):
    owner = _Owner(client, email_outbox, "shares-cursor-overflow@example.com")
    meeting_id = owner.meeting()
    first = owner.share(meeting_id)["id"]
    second = owner.share(meeting_id)["id"]
    _set_share(first, created_at=BASE_TIME + timedelta(minutes=1))
    _set_share(second, created_at=BASE_TIME)
    cursor = owner.get(limit=1).json()["next_cursor"]
    good = _decoded(cursor)

    # valid ISO times whose UTC instant is outside the datetime range: 400, not 500
    for edge in ("9999-12-31T23:59:59-01:00", "0001-01-01T00:00:00+01:00"):
        refused = owner.get(cursor=_encoded({**good, "share_created_at": edge}))
        assert refused.status_code == 400, (edge, refused.text)
        assert _error_code(refused) == "invalid_cursor"

    # allow-control: the real cursor gives the next page
    assert _ids(owner.get(limit=1, cursor=cursor)) == [second]


# --- who may read ------------------------------------------------------------


def test_only_workspace_administrators_may_list_shares(client, email_outbox, monkeypatch):
    owner, personas, _ = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"admin": "admin", "member": "member", "viewer": "viewer"},
    )
    meeting_id = _insert_meeting(owner.workspace_id, created_by=owner.user_id, title="Roles")
    created = owner.post(f"/api/v1/meetings/{meeting_id}/shares", json={})
    assert created.status_code == 201, created.text
    share_id = created.json()["id"]

    for persona in (owner, personas["admin"]):
        listed = persona.get(LIST)
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()["items"]] == [share_id]

    for name in ("member", "viewer"):
        denied = personas[name].get(LIST)
        assert denied.status_code == 403, (name, denied.text)
        assert _error_code(denied) == "permission_denied"
        assert share_id not in denied.text

    client.cookies.clear()
    anonymous = client.get(LIST, headers={"X-Workspace-ID": owner.workspace_id})
    assert anonymous.status_code == 401, anonymous.text
    assert share_id not in anonymous.text


def test_every_api_key_is_refused_even_with_the_widest_scopes(client, email_outbox):
    owner = _Owner(client, email_outbox, "shares-api-key@example.com")
    share_id = owner.share(owner.meeting())["id"]

    widest = sorted(scope.value for scope in API_KEY_SCOPES)
    minted = client.post(
        "/api/v1/api-keys",
        json={"name": "widest", "scopes": widest, "expires_in_days": 30},
        cookies=owner.cookies,
        headers=owner.headers,
    )
    assert minted.status_code == 201, minted.text
    assert set(minted.json()["scopes"]) == set(widest)
    raw_key = minted.json()["key"]

    # context: workspace:manage can never be granted to a key
    refused_mint = client.post(
        "/api/v1/api-keys",
        json={"name": "manage", "scopes": ["workspace:manage"], "expires_in_days": 30},
        cookies=owner.cookies,
        headers=owner.headers,
    )
    assert refused_mint.status_code == 422, refused_mint.text
    assert _error_code(refused_mint) == "invalid_scopes"

    client.cookies.clear()
    for headers in (
        {"Authorization": f"Bearer {raw_key}"},
        {"Authorization": f"Bearer {raw_key}", "X-Workspace-ID": owner.workspace_id},
    ):
        denied = client.get(LIST, headers=headers)
        assert denied.status_code == 403, denied.text
        assert _error_code(denied) == "api_key_scope_denied"
        assert share_id not in denied.text

    # allow-control: the same owner's session reads the list
    assert _ids(owner.get()) == [share_id]


# --- tenancy and deleted meetings --------------------------------------------


def test_list_and_cursor_stay_inside_the_workspace(client, email_outbox):
    b_owner = _Owner(client, email_outbox, "shares-tenant-b@example.com")
    b_meeting = b_owner.meeting("B meeting")
    b_shares: set[str] = set()
    for hour in range(4):
        share_id = b_owner.share(b_meeting)["id"]
        _set_share(share_id, created_at=BASE_TIME + timedelta(hours=hour))
        b_shares.add(share_id)
    a_owner = _Owner(client, email_outbox, "shares-tenant-a@example.com")
    a_meeting = a_owner.meeting("A meeting")
    a_shares: set[str] = set()
    for hour in range(3):
        share_id = a_owner.share(a_meeting)["id"]
        _set_share(share_id, created_at=BASE_TIME - timedelta(hours=hour))
        a_shares.add(share_id)

    for params in ({}, {"status": "active"}, {"audience": "workspace"}):
        walked = _walk(a_owner, limit=1, **params)
        assert set(walked) == a_shares, params
        assert set(_walk(b_owner, limit=1, **params)) == b_shares, params

    # a cursor taken from B's list, used in A, only ever pages through A's links
    b_cursor = b_owner.get(limit=1).json()["next_cursor"]
    assert isinstance(b_cursor, str)
    reused = a_owner.get(limit=100, cursor=b_cursor)
    assert set(_ids(reused)) == a_shares
    for share_id in b_shares:
        assert share_id not in reused.text
    assert "B meeting" not in reused.text

    # B's owner naming A's workspace is refused by the workspace guard
    foreign = client.get(
        LIST,
        cookies=b_owner.cookies,
        headers={"X-Workspace-ID": a_owner.workspace_id},
    )
    assert foreign.status_code == 403, foreign.text
    assert _error_code(foreign) == "workspace_access_denied"
    for share_id in a_shares:
        assert share_id not in foreign.text
    # allow-control: B's owner with B's own workspace
    assert set(_ids(b_owner.get(limit=100))) == b_shares


def test_shares_of_soft_deleted_meetings_are_not_listed(client, email_outbox):
    owner = _Owner(client, email_outbox, "shares-deleted@example.com")
    live_meeting = owner.meeting("Live meeting")
    doomed_meeting = owner.meeting("Doomed meeting")
    live = owner.share(live_meeting)["id"]
    doomed = owner.share(doomed_meeting)["id"]
    doomed_revoked = owner.share(doomed_meeting)["id"]
    owner.revoke(doomed_meeting, doomed_revoked)

    # allow-control: listed while the meeting is live
    assert set(_ids(owner.get())) == {live, doomed, doomed_revoked}

    _soft_delete_meeting(doomed_meeting)
    for params in ({}, {"status": "active"}, {"status": "revoked"}, {"audience": "workspace"}):
        response = owner.get(**params)
        assert doomed not in _ids(response) and doomed_revoked not in _ids(response), params
        assert "Doomed meeting" not in response.text
    assert _ids(owner.get()) == [live]
    assert _walk(owner, limit=1) == [live]


# --- efficiency, side effects and OpenAPI ------------------------------------


def test_the_list_reads_with_one_workspace_bound_query_and_writes_nothing(client, email_outbox):
    owner = _Owner(client, email_outbox, "shares-queries@example.com")
    meeting_id = owner.meeting()
    for _ in range(6):
        owner.share(meeting_id)

    def share_statements(**params) -> list[str]:
        seen: list[str] = []

        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            # table references, so "workspaces.public_share_links_enabled" is no match
            if any(table in statement for table in ("share_links.", "meetings.", "audit_events")):
                seen.append(statement)

        engine = get_engine()
        event.listen(engine, "before_cursor_execute", record)
        try:
            response = owner.get(**params)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert response.status_code == 200, response.text
        return seen

    with inspection_session_factory()() as db:
        audit_before = db.scalar(select(func.count(AuditEvent.id)))
    # no query per item: the page size and the filters do not matter
    statements: list[str] = []
    for params in ({"limit": 1}, {"limit": 6}, {"status": "expired", "audience": "link"}):
        found = share_statements(**params)
        assert len(found) == 1, (params, found)
        statements += found
    for statement in statements:
        # the tenant filter is in the query itself, not left to row-level security
        assert "share_links.workspace_id = " in statement, statement
        assert "meetings.workspace_id = " in statement, statement
        assert "meetings.deleted_at IS NULL" in statement, statement
        assert statement.lstrip().upper().startswith("SELECT"), statement
        assert "token_hash" not in statement, statement
        assert "share_recipients" not in statement, statement
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count(AuditEvent.id))) == audit_before


def _resolve(spec: dict, schema: dict) -> dict:
    while "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def _enum_of(spec: dict, schema: dict) -> set[str]:
    for option in schema.get("anyOf", [schema]):
        resolved = _resolve(spec, option)
        if "enum" in resolved:
            return set(resolved["enum"])
    raise AssertionError(f"no enum in {schema}")


def test_openapi_declares_typed_share_schemas(client):
    spec = client.app.openapi()

    operation = spec["paths"][LIST]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert {"limit", "cursor", "status", "audience"} <= set(parameters)
    assert _enum_of(spec, parameters["status"]["schema"]) == STATUS_KEYS
    assert _enum_of(spec, parameters["audience"]["schema"]) == AUDIENCE_KEYS
    page = _resolve(spec, operation["responses"]["200"]["content"]["application/json"]["schema"])
    assert set(page["properties"]) == {"items", "next_cursor"}
    item = _resolve(spec, page["properties"]["items"]["items"])
    assert item["type"] == "object"
    assert set(item["properties"]) == ITEM_KEYS
    assert "additionalProperties" not in item or item["additionalProperties"] is False
    assert _enum_of(spec, item["properties"]["status"]) == STATUS_KEYS
    assert _enum_of(spec, item["properties"]["audience"]) == AUDIENCE_KEYS
    assert item["properties"]["access_count"]["type"] == "integer"
