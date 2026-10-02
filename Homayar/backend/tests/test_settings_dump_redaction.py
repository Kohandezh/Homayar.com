"""Settings serialization never returns secret values in clear (HOM-160, WP02-F2)."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import pytest

from backend.config import (
    REVEAL_SECRETS_CONTEXT,
    SECRET_FIELDS,
    SECRET_REDACTED,
    Settings,
)

MARK = "fake-secret-hom160"


def _secret_values() -> dict[str, str]:
    values = {name: f"{MARK}-{name}-0123456789abcdef0123456789" for name in SECRET_FIELDS}
    values["database_url"] = f"postgresql+psycopg://user:{MARK}@db.example.invalid/homayar"
    values["redis_url"] = f"redis://:{MARK}@redis.example.invalid:6379/0"
    values["pii_mapping_keys"] = ""  # keep key-ring validation out of scope; covered as empty
    return values


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, environment="development", **_secret_values())


def test_secret_field_list_is_complete() -> None:
    assert len(SECRET_FIELDS) == 15


def test_model_dump_redacts_every_secret(settings: Settings) -> None:
    dumped = settings.model_dump()
    for name in SECRET_FIELDS:
        expected = "" if name == "pii_mapping_keys" else SECRET_REDACTED
        assert dumped[name] == expected, name
    assert MARK not in repr(dumped)


@pytest.mark.parametrize("mode", ["python", "json"])
def test_dump_modes_and_json_never_carry_a_secret(settings: Settings, mode: str) -> None:
    assert MARK not in repr(settings.model_dump(mode=mode))
    assert MARK not in settings.model_dump_json()
    assert MARK not in json.dumps(settings.model_dump(mode="json"))


def test_including_a_secret_explicitly_still_redacts(settings: Settings) -> None:
    assert settings.model_dump(include={"jwt_secret"}) == {"jwt_secret": SECRET_REDACTED}


def test_attribute_access_is_unchanged(settings: Settings) -> None:
    values = _secret_values()
    for name in SECRET_FIELDS:
        assert getattr(settings, name) == values[name], name
    copy = settings.model_copy(update={"app_name": "copied"})
    assert copy.jwt_secret == values["jwt_secret"]


def test_empty_secrets_stay_empty_not_masked() -> None:
    dumped = Settings(_env_file=None, environment="development", jwt_secret="").model_dump()
    assert dumped["jwt_secret"] == ""


def test_reveal_context_round_trips_real_values(settings: Settings) -> None:
    raw = settings.model_dump(context={REVEAL_SECRETS_CONTEXT: True})
    rebuilt = Settings(**{**raw, "_env_file": None})
    for name in SECRET_FIELDS:
        assert getattr(rebuilt, name) == getattr(settings, name), name


@pytest.mark.parametrize("context", [None, {}, {REVEAL_SECRETS_CONTEXT: "true"}, {"x": True}])
def test_only_the_exact_reveal_flag_reveals(settings: Settings, context: object) -> None:
    assert MARK not in repr(settings.model_dump(context=context))


def test_json_schema_has_no_credential_defaults() -> None:
    """Secret fields default to empty or to credential-free development URLs."""
    properties = Settings.model_json_schema()["properties"]
    for name in SECRET_FIELDS:
        default = str(properties[name].get("default", ""))
        if name in ("database_url", "redis_url"):
            assert urlsplit(default).password is None and "@" not in default, name
        else:
            assert default == "", name
