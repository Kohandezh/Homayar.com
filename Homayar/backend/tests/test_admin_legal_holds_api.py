"""HOM-192: the workspace owner's register of media objects on legal hold.

``GET /admin/legal-holds`` pages every held object of the caller's workspace,
the hold that needs review first first, with a ``due_before`` filter for holds
due for review.  It needs ``legal_hold:manage`` (owner only, never an API-key
scope), is bound to the caller's workspace in the query itself, and lists
holds of deleted conversations and of objects without a meeting too: a legal
register that hides some holds is wrong.  Holds are placed through the real
hold routes; rows are seeded with DBA writes only where a route cannot make
them (equal review dates, a second object in one conversation, no meeting).
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import event, func, select

from backend.database.relational import get_engine
from backend.database.saas_models import (
    AuditEvent,
    MediaKind,
    MediaLifecycleState,
    MediaObject,
    Workspace,
)
from backend.jobs.leases import as_utc
from backend.security.authorization import API_KEY_SCOPES, Permission
from backend.tests._database import inspection_session_factory
from backend.tests.test_admin_jobs_api import _error_code, _instant, _resolve
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _workspace_with_roles
from backend.tests.test_ingestion_ledger import _headers, _patch_media, _tenant, _upload
from backend.tests.test_legal_hold import _post
from backend.tests.test_media_lifecycle import _media_rows

LIST = "/api/v1/admin/legal-holds"
JOBS_LIST = "/api/v1/admin/jobs"
AUDIT_LIST = "/api/v1/admin/audit-events"
ITEM_KEYS = {
    "media_id",
    "kind",
    "meeting_id",
    "meeting_title",
    "reason",
    "placed_by_user_id",
    "placed_at",
    "review_at",
}
CONTENT_KEYS = {"storage_key", "sha256", "original_filename", "size_bytes"}


# --- helpers -----------------------------------------------------------------


def _base_review() -> datetime:
    """A whole-second review date a month out (the hold routes need a future one)."""
    return datetime.now(UTC).replace(microsecond=0) + timedelta(days=30)


def _get(client, tenant: dict, **params):
    return client.get(
        LIST,
        params=params,
        headers={"X-Workspace-ID": tenant["workspace_id"]},
        cookies=tenant["cookies"],
    )


def _ids(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["media_id"] for item in response.json()["items"]]


def _walk(client, tenant: dict, **params) -> list[str]:
    """Follow ``next_cursor`` to the last page; return every media id in order."""
    ids: list[str] = []
    cursor: str | None = None
    for _ in range(50):
        query = dict(params)
        if cursor is not None:
            query["cursor"] = cursor
        response = _get(client, tenant, **query)
        assert response.status_code == 200, response.text
        body = response.json()
        ids.extend(item["media_id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return ids
        assert isinstance(cursor, str) and body["items"]
    raise AssertionError("paging did not reach the last page")


def _upload_media(client, tenant: dict, key: str) -> tuple[str, str]:
    """One uploaded conversation; returns its meeting id and its ORIGINAL's id."""
    accepted = _upload(client, tenant, key=key, content=f"staged {key}".encode())
    assert accepted.status_code == 202, accepted.text
    meeting_id = accepted.json()["meeting"]["id"]
    return meeting_id, _media_rows(meeting_id)[0].id


def _hold(client, tenant: dict, media_id: str, review_at: datetime, reason: str = "litigation"):
    held = _post(
        client,
        tenant,
        f"/api/v1/media/{media_id}/legal-hold",
        {"reason": reason, "review_at": review_at.isoformat()},
    )
    assert held.status_code == 200, held.text
    assert held.json()["lifecycle_state"] == "legal_hold"


def _seed_media(
    workspace_id: str,
    *,
    meeting_id: str | None = None,
    derived_from_id: str | None = None,
    review_at: datetime | None = None,
    held_by: str | None = None,
    reason: str = "seeded hold",
) -> str:
    """One media row (DBA write); held when ``review_at`` is given.

    A row with ``derived_from_id`` is a NORMALIZED object, otherwise an
    ORIGINAL (``ck_media_lineage``); a held row carries both hold timestamps
    (``ck_media_hold``, ``ck_media_hold_review``).
    """
    held = review_at is not None
    with inspection_session_factory()() as db:
        organization_id = db.scalar(
            select(Workspace.organization_id).where(Workspace.id == workspace_id)
        )
        assert organization_id is not None
        media = MediaObject(
            organization_id=organization_id,
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            derived_from_id=derived_from_id,
            kind=MediaKind.NORMALIZED if derived_from_id else MediaKind.ORIGINAL,
            storage_backend="local",
            storage_key=f"seed/{uuid.uuid4()}",
            content_type="audio/wav",
            size_bytes=16,
            sha256="0" * 64,
            producer="seed",
            lifecycle_state=(
                MediaLifecycleState.LEGAL_HOLD if held else MediaLifecycleState.ACTIVE
            ),
            legal_hold_at=datetime.now(UTC) if held else None,
            legal_hold_reason=reason if held else None,
            legal_hold_by_user_id=held_by if held else None,
            legal_hold_review_at=review_at,
        )
        db.add(media)
        db.commit()
        return media.id


def _audit_count(workspace_id: str) -> int:
    with inspection_session_factory()() as db:
        return int(
            db.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.workspace_id == workspace_id)
            )
            or 0
        )


def _encoded(payload: object) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _get_other(client, tenant: dict, path: str) -> str:
    """The ``next_cursor`` of the first one-item page of another admin list."""
    response = client.get(
        path,
        params={"limit": 1},
        headers={"X-Workspace-ID": tenant["workspace_id"]},
        cookies=tenant["cookies"],
    )
    assert response.status_code == 200, response.text
    cursor = response.json()["next_cursor"]
    assert isinstance(cursor, str), (path, response.text)
    return cursor


@contextmanager
def _process_time_zone(zone: str) -> Iterator[None]:
    """Run with another local time zone for the whole process (``TZ``), then restore it."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = zone
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


# --- shape -------------------------------------------------------------------


def test_owner_lists_a_hold_with_exactly_the_register_fields(client, email_outbox, monkeypatch):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "register-shape")
    meeting_id, media_id = _upload_media(client, tenant, "register-shape-0001")
    review_at = _base_review()
    held = _post(
        client,
        tenant,
        f"/api/v1/meetings/{meeting_id}/legal-hold",
        {"reason": "  litigation   2026-42 ", "review_at": review_at.isoformat()},
    )
    assert held.status_code == 200, held.text
    audits_before = _audit_count(tenant["workspace_id"])

    response = _get(client, tenant)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor"}
    assert body["next_cursor"] is None
    assert len(body["items"]) == 1

    item = body["items"][0]
    assert set(item) == ITEM_KEYS
    assert not CONTENT_KEYS & set(item)
    row = _media_rows(meeting_id)[0]
    assert row.storage_key not in response.text and row.sha256 not in response.text
    assert {key: item[key] for key in ITEM_KEYS - {"placed_at", "review_at"}} == {
        "media_id": media_id,
        "kind": "original",
        "meeting_id": meeting_id,
        "meeting_title": "Weekly customer call",
        "reason": "litigation 2026-42",
        "placed_by_user_id": tenant["user_id"],
    }
    assert _instant(item["review_at"]) == review_at
    assert _instant(item["placed_at"]) == as_utc(row.legal_hold_at)

    # a read: no audit event is written
    assert _audit_count(tenant["workspace_id"]) == audits_before


def test_an_empty_register_lists_nothing(client, email_outbox):
    tenant = _tenant(client, email_outbox, "register-empty")
    response = _get(client, tenant)
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "next_cursor": None}


# --- which rows --------------------------------------------------------------


def test_only_objects_currently_on_hold_are_listed(client, email_outbox, monkeypatch):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "register-rows")
    workspace_id = tenant["workspace_id"]
    meeting_id, original_id = _upload_media(client, tenant, "register-rows-0001")
    held_id = _seed_media(workspace_id, meeting_id=meeting_id, derived_from_id=original_id)
    never_held_id = _seed_media(workspace_id, meeting_id=meeting_id, derived_from_id=original_id)
    _, other_original_id = _upload_media(client, tenant, "register-rows-0002")
    review_at = _base_review()

    _hold(client, tenant, original_id, review_at)
    _hold(client, tenant, held_id, review_at + timedelta(days=1))
    assert set(_ids(_get(client, tenant))) == {original_id, held_id}

    released = _post(
        client, tenant, f"/api/v1/media/{original_id}/legal-hold/release", {"reason": "closed"}
    )
    assert released.status_code == 200 and released.json()["lifecycle_state"] == "active"

    # allow-control: the held object of the same conversation is still listed
    listed = _ids(_get(client, tenant))
    assert listed == [held_id]
    item = _get(client, tenant).json()["items"][0]
    assert item["kind"] == "normalized" and item["meeting_id"] == meeting_id
    for absent in (original_id, never_held_id, other_original_id):
        assert absent not in listed


def test_holds_of_a_deleted_conversation_and_without_a_meeting_are_listed(
    client, email_outbox, monkeypatch
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "register-deleted")
    workspace_id = tenant["workspace_id"]
    review_at = _base_review()
    deleted_meeting_id, deleted_media_id = _upload_media(client, tenant, "register-deleted-0001")
    live_meeting_id, live_media_id = _upload_media(client, tenant, "register-deleted-0002")
    held = _post(
        client,
        tenant,
        f"/api/v1/meetings/{deleted_meeting_id}/legal-hold",
        {"reason": "litigation", "review_at": review_at.isoformat()},
    )
    assert held.status_code == 200, held.text
    _hold(client, tenant, live_media_id, review_at + timedelta(days=1))
    deleted = client.delete(
        f"/api/v1/meetings/{deleted_meeting_id}",
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert deleted.status_code == 204
    row = _media_rows(deleted_meeting_id)[0]
    assert row.lifecycle_state is MediaLifecycleState.LEGAL_HOLD
    assert row.deletion_requested_at is not None
    # no meeting row at all, and the user who placed it is gone (FK SET NULL)
    orphan_id = _seed_media(workspace_id, review_at=review_at + timedelta(days=2))

    response = _get(client, tenant)
    assert _ids(response) == [deleted_media_id, live_media_id, orphan_id]
    items = {item["media_id"]: item for item in response.json()["items"]}
    assert items[deleted_media_id]["meeting_id"] == deleted_meeting_id
    assert items[deleted_media_id]["meeting_title"] == "Weekly customer call"
    assert items[live_media_id]["meeting_id"] == live_meeting_id
    assert items[orphan_id]["meeting_id"] is None
    assert items[orphan_id]["meeting_title"] is None
    assert items[orphan_id]["placed_by_user_id"] is None
    assert items[orphan_id]["reason"] == "seeded hold"
    for item in items.values():
        assert set(item) == ITEM_KEYS


# --- due_before --------------------------------------------------------------


def test_due_before_keeps_holds_due_on_or_before_it_and_reads_naive_values_as_utc(
    client, email_outbox, monkeypatch
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "register-due")
    base = _base_review()
    on = base + timedelta(days=1)
    reviews = {
        "early": base,
        "on": on,
        "just_after": on + timedelta(hours=1),
        "later": base + timedelta(days=2),
    }
    media: dict[str, str] = {}
    for index, (name, review_at) in enumerate(reviews.items()):
        _, media_id = _upload_media(client, tenant, f"register-due-000{index}")
        _hold(client, tenant, media_id, review_at)
        media[name] = media_id

    def listed(due_before: str) -> list[str]:
        return _ids(_get(client, tenant, due_before=due_before))

    everything = [media[name] for name in ("early", "on", "just_after", "later")]
    assert _ids(_get(client, tenant)) == everything
    # the bound is inclusive; a later review date is left out
    assert listed(on.isoformat()) == [media["early"], media["on"]]
    assert listed((on - timedelta(seconds=1)).isoformat()) == [media["early"]]
    assert listed(reviews["later"].isoformat()) == everything
    # a naive value is UTC, also when the process runs in another zone: read as
    # local time (UTC-5 here, POSIX TZ, no tzdata needed) it would gain "just_after"
    with _process_time_zone("HMY+05"):
        assert datetime(2026, 1, 1).astimezone().utcoffset() == timedelta(hours=-5)
        assert listed(on.replace(tzinfo=None).isoformat()) == [media["early"], media["on"]]
    tehran = timezone(timedelta(hours=3, minutes=30))
    assert listed(on.astimezone(tehran).isoformat()) == [media["early"], media["on"]]
    assert listed((base - timedelta(days=1)).isoformat()) == []
    # the filter holds on every page
    assert _walk(client, tenant, limit=1, due_before=on.isoformat()) == [
        media["early"],
        media["on"],
    ]

    for bad in ("not-a-date", "2026-13-40T00:00:00Z", "tomorrow"):
        refused = _get(client, tenant, due_before=bad)
        assert refused.status_code == 422, (bad, refused.text)
        assert _error_code(refused) == "validation_error"


# --- order and paging --------------------------------------------------------


def test_paging_returns_every_hold_once_in_review_order_including_ties(
    client, email_outbox, monkeypatch
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "register-paging")
    workspace_id = tenant["workspace_id"]
    meeting_id, original_id = _upload_media(client, tenant, "register-paging-0001")
    # not whole seconds: a cursor that dropped the microseconds would repeat holds
    base = _base_review() + timedelta(microseconds=123456)
    tie = base + timedelta(days=5)

    first = _seed_media(workspace_id, review_at=base, held_by=tenant["user_id"])
    ties = [
        _seed_media(workspace_id, meeting_id=meeting_id, derived_from_id=original_id, review_at=tie)
        for _ in range(4)
    ]
    _hold(client, tenant, original_id, base + timedelta(days=7))
    last = _seed_media(workspace_id, review_at=base + timedelta(days=9))
    expected = [first, *sorted(ties), original_id, last]

    single_page = _get(client, tenant, limit=100)
    assert _ids(single_page) == expected
    assert single_page.json()["next_cursor"] is None
    assert _ids(_get(client, tenant)) == expected  # default limit 50

    for limit in (1, 2, 3, 5):
        assert _walk(client, tenant, limit=limit) == expected, limit

    exact = _get(client, tenant, limit=len(expected))
    assert _ids(exact) == expected and exact.json()["next_cursor"] is None
    one_short = _get(client, tenant, limit=len(expected) - 1)
    assert _ids(one_short) == expected[:-1]
    assert isinstance(one_short.json()["next_cursor"], str)

    # a page boundary inside the ties: the rest of the ties come next, once
    two = _get(client, tenant, limit=2)
    following = _get(client, tenant, limit=100, cursor=two.json()["next_cursor"])
    assert _ids(two) + _ids(following) == expected

    assert _walk(client, tenant, limit=1, due_before=tie.isoformat()) == [first, *sorted(ties)]


def test_invalid_cursors_and_limits_are_refused(client, email_outbox, monkeypatch):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "register-cursor")
    base = _base_review()
    _, first = _upload_media(client, tenant, "register-cursor-0001")
    _, second = _upload_media(client, tenant, "register-cursor-0002")
    _hold(client, tenant, first, base)
    _hold(client, tenant, second, base + timedelta(days=1))

    # real cursors of the other admin lists
    jobs_page = _get_other(client, tenant, JOBS_LIST)
    audit_page = _get_other(client, tenant, AUDIT_LIST)
    stamp = base.isoformat()
    bad_cursors = [
        "not-a-cursor!!",
        "AAAA",
        _encoded(["v", 1]),
        jobs_page,
        audit_page,
        _encoded({"v": 1, "created_at": stamp, "id": str(uuid.uuid4())}),
        _encoded({"v": 1, "occurred_at": stamp, "id": str(uuid.uuid4())}),
        _encoded({"v": 2, "review_at": stamp, "id": str(uuid.uuid4())}),
        _encoded({"v": 1, "review_at": stamp, "id": "not-a-uuid"}),
        _encoded({"v": 1, "review_at": "2030-01-01T09:00:00", "id": str(uuid.uuid4())}),
        _encoded({"v": 1, "review_at": "yesterday", "id": str(uuid.uuid4())}),
        _encoded({"v": 1, "review_at": 1, "id": str(uuid.uuid4())}),
        _encoded({"v": 1, "review_at": stamp, "id": str(uuid.uuid4()), "extra": 1}),
    ]
    for cursor in bad_cursors:
        refused = _get(client, tenant, cursor=cursor)
        assert refused.status_code == 400, (cursor, refused.text)
        assert _error_code(refused) == "invalid_cursor"

    for cursor in ("", "a" * 513):
        assert _get(client, tenant, cursor=cursor).status_code == 422
    for limit in (0, 101, -1, "many"):
        refused = _get(client, tenant, limit=limit)
        assert refused.status_code == 422, (limit, refused.text)
        assert _error_code(refused) == "validation_error"
    for limit in (1, 100):
        assert _get(client, tenant, limit=limit).status_code == 200

    # allow-control: a real next_cursor gives the next page
    page = _get(client, tenant, limit=1)
    assert _ids(page) == [first]
    following = _get(client, tenant, limit=1, cursor=page.json()["next_cursor"])
    assert _ids(following) == [second]
    assert following.json()["next_cursor"] is None


def test_out_of_range_offsets_and_loose_versions_are_refused_not_500(
    client, email_outbox, monkeypatch
):
    """A forged offset that leaves the datetime range in UTC is a bad value."""
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "register-range")
    _, media_id = _upload_media(client, tenant, "register-range-0001")
    _hold(client, tenant, media_id, _base_review())
    some_id = str(uuid.uuid4())

    for stamp in ("9999-12-31T23:59:59-01:00", "0001-01-01T00:00:00+01:00"):
        cursor = _encoded({"v": 1, "review_at": stamp, "id": some_id})
        refused = _get(client, tenant, cursor=cursor)
        assert refused.status_code == 400, (stamp, refused.text)
        assert _error_code(refused) == "invalid_cursor"

        refused = _get(client, tenant, due_before=stamp)
        assert refused.status_code == 422, (stamp, refused.text)
        assert _error_code(refused) == "invalid_due_before"

    # the version is exactly the integer 1: JSON true and 1.0 both equal 1 in Python
    for version in (True, 1.0):
        cursor = _encoded({"v": version, "review_at": _base_review().isoformat(), "id": some_id})
        refused = _get(client, tenant, cursor=cursor)
        assert refused.status_code == 400, (version, refused.text)
        assert _error_code(refused) == "invalid_cursor"

    # allow-control: the same instants inside the range are ordinary values
    latest, earliest = "9999-12-31T22:59:59-01:00", "0001-01-01T01:00:00+01:00"
    assert _ids(_get(client, tenant, due_before=latest)) == [media_id]
    assert _ids(_get(client, tenant, due_before=earliest)) == []
    from_start = _encoded({"v": 1, "review_at": earliest, "id": some_id})
    assert _ids(_get(client, tenant, cursor=from_start)) == [media_id]
    past_end = _encoded({"v": 1, "review_at": latest, "id": some_id})
    assert _ids(_get(client, tenant, cursor=past_end)) == []


# --- who may read ------------------------------------------------------------


def test_only_the_owner_may_read_the_register(client, email_outbox, monkeypatch):
    _patch_media(monkeypatch)
    owner, personas, _ = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"admin": "admin", "member": "member", "viewer": "viewer"},
    )
    tenant = {
        "user_id": owner.user_id,
        "workspace_id": owner.workspace_id,
        "cookies": owner.cookies,
    }
    _, media_id = _upload_media(client, tenant, "register-roles-0001")
    _hold(client, tenant, media_id, _base_review())

    listed = owner.get(LIST)
    assert listed.status_code == 200, listed.text
    assert [item["media_id"] for item in listed.json()["items"]] == [media_id]

    for name in ("admin", "member", "viewer"):
        denied = personas[name].get(LIST)
        assert denied.status_code == 403, (name, denied.text)
        assert _error_code(denied) == "permission_denied"
        assert media_id not in denied.text
        # the filters do not open another way in
        filtered = personas[name].get(LIST, params={"due_before": _base_review().isoformat()})
        assert filtered.status_code == 403, (name, filtered.text)

    client.cookies.clear()
    anonymous = client.get(LIST, headers={"X-Workspace-ID": owner.workspace_id})
    assert anonymous.status_code == 401, anonymous.text
    assert media_id not in anonymous.text


def test_every_api_key_is_refused_even_the_owners_widest(client, email_outbox, monkeypatch):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "register-keys")
    _, media_id = _upload_media(client, tenant, "register-keys-0001")
    _hold(client, tenant, media_id, _base_review())
    headers = _headers(tenant)

    # legal_hold:manage cannot be granted to a key at all
    refused = _mint(client, headers, tenant["cookies"], scopes=[Permission.LEGAL_HOLD_MANAGE.value])
    assert refused.status_code == 422 and _error_code(refused) == "invalid_scopes"
    widest = _mint(
        client,
        headers,
        tenant["cookies"],
        name="widest",
        scopes=sorted(scope.value for scope in API_KEY_SCOPES),
    )
    assert widest.status_code == 201, widest.text
    narrow = _mint(client, headers, tenant["cookies"], name="narrow", scopes=["meeting:read"])
    assert narrow.status_code == 201, narrow.text

    # allow-control: the owner's own session reads the register
    assert _ids(_get(client, tenant)) == [media_id]

    client.cookies.clear()
    for raw_key in (widest.json()["key"], narrow.json()["key"]):
        assert client.get("/api/v1/meetings", headers=_bearer(raw_key)).status_code == 200
        for key_headers in (
            _bearer(raw_key),
            _bearer(raw_key, **{"X-Workspace-ID": tenant["workspace_id"]}),
        ):
            denied = client.get(LIST, headers=key_headers)
            assert denied.status_code == 403, denied.text
            assert _error_code(denied) == "api_key_scope_denied"
            assert media_id not in denied.text


# --- tenancy -----------------------------------------------------------------


def test_register_and_cursor_stay_inside_the_workspace(client, email_outbox, monkeypatch):
    _patch_media(monkeypatch)
    base = _base_review()
    a_tenant = _tenant(client, email_outbox, "register-tenant-a")
    a_holds = []
    for index in range(3):
        _, media_id = _upload_media(client, a_tenant, f"register-tenant-a-000{index}")
        _hold(client, a_tenant, media_id, base + timedelta(days=index))
        a_holds.append(media_id)
    b_tenant = _tenant(client, email_outbox, "register-tenant-b")
    b_holds = []
    for index in range(2):
        _, media_id = _upload_media(client, b_tenant, f"register-tenant-b-000{index}")
        # B's review dates sit between A's, so a shared query would interleave them
        _hold(client, b_tenant, media_id, base + timedelta(days=index, hours=12))
        b_holds.append(media_id)

    assert _walk(client, a_tenant, limit=1) == a_holds
    assert _walk(client, b_tenant, limit=1) == b_holds
    far = (base + timedelta(days=30)).isoformat()
    assert _walk(client, b_tenant, limit=1, due_before=far) == b_holds

    # a cursor taken from A's register, used in B, only ever pages through B's holds
    a_cursor = _get(client, a_tenant, limit=1).json()["next_cursor"]
    assert isinstance(a_cursor, str)
    reused = _get(client, b_tenant, limit=100, cursor=a_cursor)
    assert _ids(reused) == b_holds
    for media_id in a_holds:
        assert media_id not in reused.text

    # B's owner naming A's workspace is refused by the workspace guard
    foreign = client.get(
        LIST, headers={"X-Workspace-ID": a_tenant["workspace_id"]}, cookies=b_tenant["cookies"]
    )
    assert foreign.status_code == 403, foreign.text
    assert _error_code(foreign) == "workspace_access_denied"
    for media_id in a_holds:
        assert media_id not in foreign.text


def test_register_reads_with_one_workspace_bound_query(client, email_outbox, monkeypatch):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "register-queries")
    workspace_id = tenant["workspace_id"]
    meeting_id, original_id = _upload_media(client, tenant, "register-queries-0001")
    base = _base_review()
    _hold(client, tenant, original_id, base)
    for day in range(1, 6):
        _seed_media(
            workspace_id,
            meeting_id=meeting_id,
            derived_from_id=original_id,
            review_at=base + timedelta(days=day),
        )

    def media_statements(**params) -> list[str]:
        seen: list[str] = []

        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            if "media_objects" in statement:
                seen.append(statement)

        engine = get_engine()
        event.listen(engine, "before_cursor_execute", record)
        try:
            response = _get(client, tenant, **params)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert response.status_code == 200, response.text
        return seen

    # no query per item (meeting): the page size does not matter
    for params in ({"limit": 1}, {"limit": 6}, {"limit": 2, "due_before": base.isoformat()}):
        statements = media_statements(**params)
        assert len(statements) == 1, (params, statements)
        # the tenant filter is in the query itself, the meeting join included
        assert "media_objects.workspace_id = " in statements[0], statements[0]
        assert "meetings.workspace_id = " in statements[0], statements[0]


# --- OpenAPI -----------------------------------------------------------------


def test_openapi_declares_the_typed_register_schema(client):
    spec = client.app.openapi()
    operation = spec["paths"][LIST]["get"]
    assert {"limit", "cursor", "due_before"} <= {
        parameter["name"] for parameter in operation["parameters"]
    }
    page = _resolve(spec, operation["responses"]["200"]["content"]["application/json"]["schema"])
    assert set(page["properties"]) == {"items", "next_cursor"}
    item = _resolve(spec, page["properties"]["items"]["items"])
    assert item["type"] == "object"
    assert set(item["properties"]) == ITEM_KEYS
    assert "additionalProperties" not in item or item["additionalProperties"] is False
    kind = _resolve(spec, item["properties"]["kind"])
    assert kind["type"] == "string" and set(kind["enum"]) == {member.value for member in MediaKind}
