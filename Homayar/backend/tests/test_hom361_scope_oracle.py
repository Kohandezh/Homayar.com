"""HOM-361 oracle: an API key acts with ``stored scopes ∩ API_KEY_SCOPES``."""

from __future__ import annotations

import logging

import pytest

from backend.database.saas_models import ApiKey, LoginSession, User
from backend.retrieval.evidence import _live_key_scopes
from backend.security.authorization import API_KEY_SCOPES, Permission
from backend.security.context import Actor
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _error, _mint, _owner
from backend.tests.test_share_policies import _anonymous

LOGGER = "backend.security.context"
ALLOWED = sorted(permission.value for permission in API_KEY_SCOPES)
FORBIDDEN = sorted(
    permission.value for permission in Permission if permission not in API_KEY_SCOPES
)
EVERY_PERMISSION = sorted(permission.value for permission in Permission)
UNKNOWN_SPELLINGS = (
    "not-a-scope",
    "MEETING:READ",
    "Meeting:Read",
    "MEETING_READ",
    "meeting_read",
    "meeting.read",
    " meeting:read",
    "meeting:read ",
    "",
    "SCORECARD:READ",
    "scorecard_read",
)
UNKNOWN_TEXT = "zz-hom361-unknown-stored-text"
KEY_NAME = "hom361-oracle-key-name"
_RECORD_FIELDS = set(vars(logging.LogRecord("x", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
}


def _key_actor(scopes) -> Actor:
    return Actor(
        user=User(),
        session=None,
        token_source="api_key",  # noqa: S106
        api_key=ApiKey(scopes=list(scopes)),
    )


def _store_scopes(key_id: str, scopes: list[str]) -> ApiKey:
    with inspection_session_factory()() as db:
        row = db.get(ApiKey, key_id)
        row.scopes = list(scopes)
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row


def _records(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == LOGGER]


def _record_text(record: logging.LogRecord) -> str:
    extras = {key: value for key, value in vars(record).items() if key not in _RECORD_FIELDS}
    return f"{record.getMessage()} {record.args!r} {extras!r}"


@pytest.mark.parametrize("forbidden", FORBIDDEN)
def test_a1_a_stored_forbidden_scope_is_absent_from_actor_scopes(forbidden):
    assert _key_actor(["meeting:read", forbidden]).scopes == {Permission.MEETING_READ}


@pytest.mark.parametrize("forbidden", FORBIDDEN)
def test_a1_control_the_allowed_scope_of_the_same_row_is_present(forbidden):
    assert Permission.MEETING_READ in _key_actor(["meeting:read", forbidden]).scopes


def test_a1_a_row_of_only_forbidden_scopes_gives_no_scope():
    assert _key_actor(FORBIDDEN).scopes == frozenset()


def test_a1_control_a_session_actor_has_no_scope_limit():
    actor = Actor(user=User(), session=LoginSession(), token_source="cookie")  # noqa: S106
    assert actor.scopes is None


def test_a4_every_permission_stored_gives_exactly_api_key_scopes():
    assert _key_actor(EVERY_PERMISSION).scopes == API_KEY_SCOPES


def test_a4_control_every_allowed_scope_is_kept():
    assert _key_actor(ALLOWED).scopes == API_KEY_SCOPES


@pytest.mark.parametrize(
    "subset",
    [
        (),
        ("meeting:read",),
        ("meeting:create", "meeting:update"),
        ("member:read", "team:read", "billing:read", "audit:read"),
        ("audit:read", "meeting:read", "audit:read"),
    ],
)
def test_a4_control_a_subset_of_allowed_scopes_is_kept_exactly(subset):
    assert _key_actor(subset).scopes == {Permission(value) for value in subset}


@pytest.mark.parametrize("subset", [("meeting:read",), ("team:read", "billing:read")])
def test_a4_a_subset_mixed_with_forbidden_scopes_keeps_exactly_the_subset(subset):
    stored = [*FORBIDDEN, *subset]
    assert _key_actor(stored).scopes == {Permission(value) for value in subset}


@pytest.mark.parametrize("unknown", UNKNOWN_SPELLINGS)
def test_a5_control_an_unknown_stored_string_is_skipped_without_error(unknown):
    assert _key_actor(["meeting:read", unknown]).scopes == {Permission.MEETING_READ}


def test_a5_control_misspelled_allowed_scopes_grant_nothing():
    assert _key_actor(["MEETING:READ", " meeting:read", "meeting_read"]).scopes == frozenset()


@pytest.mark.parametrize("unknown", ["not-a-scope", "SCORECARD:READ"])
def test_a5_unknown_and_forbidden_values_are_both_dropped(unknown):
    stored = [unknown, "scorecard:read", "meeting:read", "api_key:manage"]
    assert _key_actor(stored).scopes == {Permission.MEETING_READ}


@pytest.mark.parametrize("forbidden", ["scorecard:read", "api_key:manage", "notification:read"])
def test_a7_live_key_scopes_drops_a_forbidden_scope(forbidden):
    row = ApiKey(scopes=["meeting:read", forbidden])
    assert _live_key_scopes(row) == {Permission.MEETING_READ}


def test_a7_live_key_scopes_of_every_permission_is_api_key_scopes():
    assert _live_key_scopes(ApiKey(scopes=EVERY_PERMISSION)) == API_KEY_SCOPES


def test_a7_control_live_key_scopes_keeps_allowed_scopes_and_skips_unknown():
    assert _live_key_scopes(ApiKey(scopes=ALLOWED)) == API_KEY_SCOPES
    row = ApiKey(scopes=["meeting:read", "not-a-scope", "MEETING:READ"])
    assert _live_key_scopes(row) == {Permission.MEETING_READ}


@pytest.mark.parametrize(
    "stored",
    [
        ("meeting:read",),
        ("meeting:read", "scorecard:read"),
        ("not-a-scope", "api_key:manage", "team:read"),
        tuple(EVERY_PERMISSION),
    ],
)
def test_a7_control_live_key_scopes_matches_actor_scopes(stored):
    assert _live_key_scopes(ApiKey(scopes=list(stored))) == _key_actor(stored).scopes


def test_effective_api_key_scopes_intersects_and_does_not_log(caplog):
    from backend.security.context import effective_api_key_scopes

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        mixed = effective_api_key_scopes(
            ["meeting:read", "scorecard:read", "not-a-scope", "api_key:manage"]
        )
        every = effective_api_key_scopes(value for value in EVERY_PERMISSION)
        empty = effective_api_key_scopes([])
    assert isinstance(mixed, frozenset)
    assert mixed == {Permission.MEETING_READ}
    assert every == API_KEY_SCOPES
    assert empty == frozenset()
    assert _records(caplog) == []


@pytest.mark.parametrize(
    ("forbidden", "path"),
    [
        ("scorecard:read", "/api/v1/scorecards"),
        ("notification:read", "/api/v1/notifications"),
    ],
)
def test_a2_a3_a_stored_forbidden_scope_is_refused_and_the_allowed_scope_still_reads(
    client, email_outbox, forbidden, path
):
    _, _, cookies, headers = _owner(client, email_outbox)
    minted = _mint(client, headers, cookies, name=KEY_NAME, scopes=["meeting:read"])
    assert minted.status_code == 201, minted.text
    raw_key = minted.json()["key"]
    session_read = client.get(path, headers=headers, cookies=cookies)
    assert session_read.status_code == 200, session_read.text
    _store_scopes(minted.json()["id"], ["meeting:read", forbidden])
    _anonymous(client)

    refused = client.get(path, headers=_bearer(raw_key))
    assert refused.status_code == 403, refused.text
    assert _error(refused) == "api_key_scope_denied"

    allowed = client.get("/api/v1/meetings", headers=_bearer(raw_key))
    assert allowed.status_code == 200, allowed.text


def test_a3_control_a_key_with_a_stored_forbidden_scope_still_reads_meetings(client, email_outbox):
    _, _, cookies, headers = _owner(client, email_outbox)
    minted = _mint(client, headers, cookies, name=KEY_NAME, scopes=["meeting:read"])
    assert minted.status_code == 201, minted.text
    _store_scopes(minted.json()["id"], ["meeting:read", "scorecard:read"])
    _anonymous(client)

    allowed = client.get("/api/v1/meetings", headers=_bearer(minted.json()["key"]))
    assert allowed.status_code == 200, allowed.text


def test_a6_a_dropped_scope_logs_one_warning_without_secrets(client, email_outbox, caplog):
    credentials, workspace_id, cookies, headers = _owner(client, email_outbox)
    dropping = _mint(client, headers, cookies, name=KEY_NAME, scopes=["meeting:read"])
    assert dropping.status_code == 201, dropping.text
    raw_key = dropping.json()["key"]
    row = _store_scopes(
        dropping.json()["id"],
        ["scorecard:read", "meeting:read", UNKNOWN_TEXT, "api_key:manage"],
    )
    _anonymous(client)
    secrets = (
        raw_key,
        raw_key.split(".", 1)[-1],
        row.key_hash,
        row.key_prefix,
        UNKNOWN_TEXT,
        KEY_NAME,
        credentials["email"],
        "Workspace Owner",
    )

    for path, status in (("/api/v1/meetings", 200), ("/api/v1/scorecards", 403)):
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger=LOGGER):
            response = client.get(path, headers=_bearer(raw_key))
        assert response.status_code == status, response.text
        warnings = [record for record in _records(caplog) if record.levelno >= logging.WARNING]
        assert len(warnings) == 1, [record.getMessage() for record in warnings]
        assert warnings[0].levelno == logging.WARNING
        message = warnings[0].getMessage()
        assert row.id in message
        assert workspace_id in message
        assert "scorecard:read" in message and "api_key:manage" in message
        assert message.index("api_key:manage") < message.index("scorecard:read")
        for record in _records(caplog):
            text = _record_text(record)
            for secret in secrets:
                assert secret not in text, (path, secret)


def test_a6_control_a_key_with_only_allowed_scopes_logs_nothing(client, email_outbox, caplog):
    _, _, cookies, headers = _owner(client, email_outbox)
    clean = _mint(client, headers, cookies, name="clean", scopes=["meeting:read", "meeting:create"])
    assert clean.status_code == 201, clean.text
    _anonymous(client)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        response = client.get("/api/v1/meetings", headers=_bearer(clean.json()["key"]))
    assert response.status_code == 200, response.text
    assert [r for r in _records(caplog) if r.levelno >= logging.WARNING] == []
