"""HOM-361: stored API key scopes are intersected with ``API_KEY_SCOPES``.

A key acts with ``scopes ∩ API_KEY_SCOPES ∩ current membership permissions``
(ADR 0013).  Creation refuses values outside ``API_KEY_SCOPES``, but a stored
row can still hold one: a bug in key creation, a direct database edit, or an
old key from before a scope was removed.  Every reader of stored scopes -
``Actor.scopes`` and the retrieval re-check ``_live_key_scopes`` - must apply
the same intersection, and a request whose stored scopes were narrowed logs
one ``WARNING`` that carries ids and counts, never key material or the text of
an unknown stored value.
"""

from __future__ import annotations

import logging

from backend.database.saas_models import ApiKey, User
from backend.retrieval.evidence import _live_key_scopes
from backend.security.authorization import API_KEY_SCOPES, Permission
from backend.security.context import Actor
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _anonymous, _bearer, _error, _mint, _owner

CONTEXT_LOGGER = "backend.security.context"


def _minted(client, email_outbox, *, name="ci", scopes=("meeting:read",)):
    """An owner-minted key and its workspace; the client is anonymous after."""
    _, workspace_id, cookies, headers = _owner(client, email_outbox)
    created = _mint(client, headers, cookies, name=name, scopes=scopes)
    assert created.status_code == 201, created.text
    _anonymous(client)
    return created.json(), workspace_id


def _stored(key_id: str, scopes: list[str]) -> None:
    """Edit the stored scopes of a key row directly (the HOM-361 premise)."""
    with inspection_session_factory()() as db:
        row = db.get(ApiKey, key_id)
        assert row is not None
        row.scopes = list(scopes)
        db.commit()


def _actor_scopes(key_id: str) -> frozenset[Permission]:
    """``Actor.scopes`` for the bound user of the stored key row."""
    with inspection_session_factory()() as db:
        key = db.get(ApiKey, key_id)
        assert key is not None
        user = db.get(User, key.created_by_user_id)
        assert user is not None
        return Actor(
            user=user,
            session=None,
            token_source="api_key",  # noqa: S106 - transport label, not a secret
            api_key=key,
        ).scopes


def test_actor_scopes_intersect_stored_scopes_with_api_key_scopes(client, email_outbox):
    # deny-case: valid permissions outside API_KEY_SCOPES are dropped
    minted, _ = _minted(client, email_outbox)
    _stored(minted["id"], ["meeting:read", "scorecard:read", "api_key:manage"])
    assert _actor_scopes(minted["id"]) == frozenset({Permission.MEETING_READ})

    # allow-control: an allowed scope from the same row is kept
    _stored(minted["id"], ["meeting:read"])
    assert _actor_scopes(minted["id"]) == frozenset({Permission.MEETING_READ})


def test_role_granted_forbidden_stored_scope_is_403_and_meeting_read_still_works(
    client,
    email_outbox,
):
    minted, workspace_id = _minted(client, email_outbox)
    _stored(minted["id"], ["meeting:read", "scorecard:read"])

    # deny-case: the owner role has scorecard:read, the stored scope must not
    # carry it (the 403 is the scope check of the permission guard)
    refused = client.get("/api/v1/scorecards", headers=_bearer(minted["key"]))
    assert refused.status_code == 403, refused.text
    assert _error(refused) == "api_key_scope_denied"

    # allow-control: the same key still reads a meeting:read route
    assert client.get("/api/v1/meetings", headers=_bearer(minted["key"])).status_code == 200


def test_no_allowed_scope_is_lost(client, email_outbox):
    every = sorted(scope.value for scope in API_KEY_SCOPES)
    widest, _ = _minted(client, email_outbox, name="every", scopes=every)
    assert _actor_scopes(widest["id"]) == frozenset(API_KEY_SCOPES)

    subset, _ = _minted(client, email_outbox, name="subset", scopes=("meeting:read", "team:read"))
    assert _actor_scopes(subset["id"]) == frozenset({Permission.MEETING_READ, Permission.TEAM_READ})


def test_unknown_stored_strings_are_skipped_without_error(client, email_outbox):
    minted, _ = _minted(client, email_outbox)
    _stored(minted["id"], ["meeting:read", "not-a-scope"])
    assert _actor_scopes(minted["id"]) == frozenset({Permission.MEETING_READ})


def test_narrowed_stored_scopes_log_one_warning_with_ids_only(client, email_outbox, caplog):
    minted, workspace_id = _minted(client, email_outbox)
    _stored(minted["id"], ["meeting:read", "scorecard:read", "not-a-scope"])
    with inspection_session_factory()() as db:
        row = db.get(ApiKey, minted["id"])
        key_hash, key_prefix, key_name = row.key_hash, row.key_prefix, row.name
        owner = db.get(User, row.created_by_user_id)
        assert owner is not None
        owner_email = owner.email

    # deny-case: the narrowed key still works on a meeting:read route, and
    # that request logs exactly one WARNING (checked before any 403, so the
    # base fails at a log assertion first)
    with caplog.at_level(logging.WARNING, logger=CONTEXT_LOGGER):
        ok = client.get("/api/v1/meetings", headers=_bearer(minted["key"]))
    assert ok.status_code == 200, ok.text
    records = [record for record in caplog.records if record.name == CONTEXT_LOGGER]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.WARNING
    message = record.getMessage()
    assert minted["id"] in message
    assert workspace_id in message
    assert "scorecard:read" in message
    never_logged = (
        minted["key"],
        key_hash,
        key_prefix,
        key_name,
        owner_email,
        "not-a-scope",
    )
    for value in never_logged:
        assert value not in message, value
        assert value not in record.args, value

    # then the 403: the owner role has scorecard:read, the stored scope must
    # not carry it (the scope check of the permission guard)
    refused = client.get("/api/v1/scorecards", headers=_bearer(minted["key"]))
    assert refused.status_code == 403, refused.text
    assert _error(refused) == "api_key_scope_denied"

    # allow-control: a row holding only allowed scopes logs nothing
    quiet, _ = _minted(client, email_outbox, name="quiet")
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=CONTEXT_LOGGER):
        assert client.get("/api/v1/meetings", headers=_bearer(quiet["key"])).status_code == 200
    assert [record for record in caplog.records if record.name == CONTEXT_LOGGER] == []


def test_live_key_scopes_applies_the_same_intersection():
    assert _live_key_scopes(ApiKey(scopes=["meeting:read", "scorecard:read"])) == frozenset(
        {Permission.MEETING_READ}
    )
    assert _live_key_scopes(ApiKey(scopes=["meeting:read", "not-a-scope"])) == frozenset(
        {Permission.MEETING_READ}
    )


def test_effective_api_key_scopes_intersects_the_fixed_set():
    from backend.security.context import effective_api_key_scopes

    assert effective_api_key_scopes(["meeting:read", "scorecard:read"]) == frozenset(
        {Permission.MEETING_READ}
    )
    assert effective_api_key_scopes(sorted(scope.value for scope in API_KEY_SCOPES)) == frozenset(
        API_KEY_SCOPES
    )
    assert effective_api_key_scopes(["not-a-scope"]) == frozenset()
