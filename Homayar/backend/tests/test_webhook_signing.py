"""HOM-89: webhook signing, secrets, key ids, payload bytes and secret encryption.

These are the pure parts of signed webhook delivery (DESIGN sections 3 and 4).
They need no database. The delivery worker and the API build on them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta, timezone

import pytest
from homayar_client import WebhookVerificationError, verify_webhook
from pydantic import ValidationError

from backend.config import SECRET_FIELDS, Settings
from backend.observability.scrub import (
    REDACTED,
    diagnostic_text,
    looks_like_secret,
    scrub_text,
)
from backend.privacy.mapping_crypto import parse_keyring
from backend.services.webhook_signing import (
    KEY_ID_PREFIX,
    SECRET_PREFIX,
    USER_AGENT,
    WebhookSecretCipher,
    WebhookSecretIntegrityError,
    WebhookSigningUnavailable,
    encode_payload,
    new_key_id,
    new_signing_secret,
    parse_webhook_keyring,
    request_headers,
    signature,
    signature_header,
    signed_content,
)

KEY_ONE = base64.b64encode(b"webhook-test-key-one-32-bytes-QQ").decode()
KEY_TWO = base64.b64encode(b"webhook-test-key-two-32-bytes-QQ").decode()
EVENT_ID = "2f8d3c1e-7a4b-4c55-9e1a-0b6d2f9c8e71"
WORKSPACE_ID = "ws-1"
ENDPOINT_ID = "ep-1"


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def _cipher(keys: str = f"k1:{KEY_ONE}", active: str = "k1") -> WebhookSecretCipher:
    cipher = WebhookSecretCipher.from_settings(
        _settings(webhook_secret_keys=keys, webhook_secret_active_key_id=active)
    )
    assert cipher is not None
    return cipher


# --- secrets and key ids --------------------------------------------------------------


def test_signing_secret_is_whsec_and_base64url_of_32_random_bytes() -> None:
    secret = new_signing_secret()

    assert SECRET_PREFIX == "whsec_"  # noqa: S105 - a prefix
    assert len(secret) == 49
    assert re.fullmatch(r"whsec_[A-Za-z0-9_-]{43}", secret)
    encoded = secret.removeprefix("whsec_")
    assert len(base64.urlsafe_b64decode(encoded + "=")) == 32
    assert new_signing_secret() != secret


def test_key_id_is_whk_and_24_lowercase_hex_chars() -> None:
    key_id = new_key_id()

    assert KEY_ID_PREFIX == "whk_"
    assert re.fullmatch(r"whk_[0-9a-f]{24}", key_id)
    assert len(key_id) == 28
    assert new_key_id() != key_id


# --- signing --------------------------------------------------------------------------


def test_signed_content_is_id_dot_timestamp_dot_raw_body() -> None:
    body = b'{"a":1}'

    assert signed_content(EVENT_ID, 1_790_000_000, body) == (
        f"{EVENT_ID}.1790000000.".encode("ascii") + body
    )


def test_signature_is_hmac_sha256_hex_keyed_with_the_full_secret() -> None:
    secret = new_signing_secret()
    body = '{"text":"سلام"}'.encode()
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{EVENT_ID}.1790000000.".encode("ascii") + body,
        hashlib.sha256,
    ).hexdigest()

    value = signature(secret, event_id=EVENT_ID, timestamp=1_790_000_000, body=body)

    assert value == expected
    assert re.fullmatch(r"[0-9a-f]{64}", value)


def test_signature_header_has_one_v1_entry_per_key_in_the_given_order() -> None:
    first, second = new_signing_secret(), new_signing_secret()
    body = b"{}"

    header = signature_header(
        [("whk_" + "a" * 24, first), ("whk_" + "b" * 24, second)],
        event_id=EVENT_ID,
        timestamp=10,
        body=body,
    )

    one = signature(first, event_id=EVENT_ID, timestamp=10, body=body)
    two = signature(second, event_id=EVENT_ID, timestamp=10, body=body)
    assert header == f"v1=whk_{'a' * 24}:{one},v1=whk_{'b' * 24}:{two}"


def test_signature_header_needs_at_least_one_key() -> None:
    with pytest.raises(ValueError):
        signature_header([], event_id=EVENT_ID, timestamp=10, body=b"{}")


def test_request_headers_are_exact() -> None:
    secret = new_signing_secret()
    key_id = new_key_id()
    body = b'{"id":"x"}'

    headers = request_headers([(key_id, secret)], event_id=EVENT_ID, timestamp=1234, body=body)

    assert USER_AGENT == "Homayar-Webhooks/1"
    assert headers == {
        "Content-Type": "application/json",
        "User-Agent": "Homayar-Webhooks/1",
        "Homayar-Webhook-Id": EVENT_ID,
        "Homayar-Webhook-Timestamp": "1234",
        "Homayar-Webhook-Signature": (
            f"v1={key_id}:{signature(secret, event_id=EVENT_ID, timestamp=1234, body=body)}"
        ),
    }
    assert all(secret not in value for value in headers.values())


@pytest.mark.parametrize("timestamp", [-1, True, 1.5])
def test_timestamp_is_a_non_negative_integer(timestamp) -> None:
    with pytest.raises((TypeError, ValueError)):
        signed_content(EVENT_ID, timestamp, b"{}")


def test_signed_request_verifies_with_the_sdk() -> None:
    secret = new_signing_secret()
    key_id = new_key_id()
    body = encode_payload(
        event_id=EVENT_ID,
        event_type="webhook.test",
        workspace_id=WORKSPACE_ID,
        data={"endpoint_id": ENDPOINT_ID},
        created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
    )
    headers = request_headers([(key_id, secret)], event_id=EVENT_ID, timestamp=1000, body=body)

    event = verify_webhook(headers=headers, body=body, secrets={key_id: secret}, now=1000)

    assert event["id"] == EVENT_ID
    with pytest.raises(WebhookVerificationError) as caught:
        verify_webhook(headers=headers, body=body + b" ", secrets={key_id: secret}, now=1000)
    assert caught.value.code == "invalid_signature"


# --- log and audit scrubbing (SPEC A4.8) ------------------------------------------------


@pytest.mark.parametrize("render", [scrub_text, diagnostic_text])
def test_log_scrubbing_hides_a_signing_secret(render) -> None:
    secret = new_signing_secret()
    # Fixed: a random hex id could hold a digit run that diagnostic_text masks as personal data.
    key_id = "whk_" + "abcdef" * 4

    text = render(f"rotated {key_id} to {secret}, endpoint ep-1")

    assert secret not in text
    assert secret.removeprefix("whsec_") not in text
    assert f"rotated {key_id} to {REDACTED}, endpoint ep-1" == text


def test_an_audit_value_that_is_a_signing_secret_is_redacted() -> None:
    # record_audit_event replaces a value for which looks_like_secret is true.
    assert looks_like_secret(new_signing_secret())
    assert not looks_like_secret(new_key_id())
    assert not looks_like_secret("whsec_short")


# --- payload envelope ------------------------------------------------------------------


def test_payload_is_compact_sorted_utf8_json() -> None:
    body = encode_payload(
        event_id=EVENT_ID,
        event_type="webhook.test",
        workspace_id=WORKSPACE_ID,
        data={"endpoint_id": ENDPOINT_ID, "note": "تست"},
        created_at=datetime(2026, 10, 30, 12, 0, 5, 987654, tzinfo=UTC),
    )

    envelope = {
        "created_at": "2026-10-30T12:00:05Z",
        "data": {"endpoint_id": ENDPOINT_ID, "note": "تست"},
        "id": EVENT_ID,
        "type": "webhook.test",
        "workspace_id": WORKSPACE_ID,
    }
    assert body == json.dumps(
        envelope, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    assert "تست".encode() in body
    assert body.startswith(b'{"created_at":"2026-10-30T12:00:05Z","data":')


def test_payload_created_at_is_converted_to_utc() -> None:
    tehran = timezone(timedelta(hours=3, minutes=30))
    body = encode_payload(
        event_id=EVENT_ID,
        event_type="webhook.test",
        workspace_id=WORKSPACE_ID,
        data={},
        created_at=datetime(2026, 10, 30, 15, 30, tzinfo=tehran),
    )

    assert json.loads(body)["created_at"] == "2026-10-30T12:00:00Z"


# --- secrets at rest -------------------------------------------------------------------


def test_cipher_is_none_without_a_keyring() -> None:
    assert WebhookSecretCipher.from_settings(_settings()) is None


def test_encrypt_and_decrypt_round_trip_with_a_fresh_12_byte_nonce() -> None:
    cipher = _cipher()
    secret = new_signing_secret()
    bound = {"workspace_id": WORKSPACE_ID, "endpoint_id": ENDPOINT_ID, "key_id": "whk_1"}

    key_id, nonce, ciphertext = cipher.encrypt(secret, **bound)
    _, other_nonce, other_ciphertext = cipher.encrypt(secret, **bound)

    assert key_id == "k1"
    assert len(nonce) == 12
    assert nonce != other_nonce
    assert ciphertext != other_ciphertext
    assert secret.encode() not in ciphertext
    assert (
        cipher.decrypt(encryption_key_id=key_id, nonce=nonce, ciphertext=ciphertext, **bound)
        == secret
    )


@pytest.mark.parametrize(
    "moved",
    [
        {"workspace_id": "ws-2"},
        {"endpoint_id": "ep-2"},
        {"key_id": "whk_2"},
    ],
)
def test_associated_data_binds_workspace_endpoint_and_key_id(moved: dict) -> None:
    cipher = _cipher()
    secret = new_signing_secret()
    bound = {"workspace_id": WORKSPACE_ID, "endpoint_id": ENDPOINT_ID, "key_id": "whk_1"}
    key_id, nonce, ciphertext = cipher.encrypt(secret, **bound)

    with pytest.raises(WebhookSecretIntegrityError) as caught:
        cipher.decrypt(
            encryption_key_id=key_id, nonce=nonce, ciphertext=ciphertext, **(bound | moved)
        )
    assert secret not in str(caught.value)


def test_keyring_rotation_keeps_older_rows_readable() -> None:
    secret = new_signing_secret()
    bound = {"workspace_id": WORKSPACE_ID, "endpoint_id": ENDPOINT_ID, "key_id": "whk_1"}
    old_key_id, nonce, ciphertext = _cipher().encrypt(secret, **bound)

    rotated = _cipher(keys=f"k1:{KEY_ONE},k2:{KEY_TWO}", active="k2")

    assert rotated.active_key_id == "k2"
    assert rotated.encrypt(secret, **bound)[0] == "k2"
    assert rotated.has_key("k1") and rotated.has_key("k2")
    assert not rotated.has_key("k3")
    assert (
        rotated.decrypt(encryption_key_id=old_key_id, nonce=nonce, ciphertext=ciphertext, **bound)
        == secret
    )


def test_a_removed_keyring_key_makes_signing_unavailable() -> None:
    secret = new_signing_secret()
    bound = {"workspace_id": WORKSPACE_ID, "endpoint_id": ENDPOINT_ID, "key_id": "whk_1"}
    key_id, nonce, ciphertext = _cipher().encrypt(secret, **bound)

    later = _cipher(keys=f"k2:{KEY_TWO}", active="k2")

    with pytest.raises(WebhookSigningUnavailable) as caught:
        later.decrypt(encryption_key_id=key_id, nonce=nonce, ciphertext=ciphertext, **bound)
    assert secret not in str(caught.value)


# --- keyring parsing and settings ------------------------------------------------------


def test_parse_keyring_default_messages_are_unchanged() -> None:
    with pytest.raises(ValueError, match=r"^PII_MAPPING_KEYS entries must be key_id:base64key$"):
        parse_keyring("no-colon")
    with pytest.raises(ValueError, match=r"^PII_MAPPING_KEYS contains an invalid base64 key$"):
        parse_keyring("k1:not base64!")
    with pytest.raises(ValueError, match=r"^PII_MAPPING_KEYS keys must be 32 bytes \(AES-256\)$"):
        parse_keyring(f"k1:{base64.b64encode(b'short').decode()}")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("no-colon", r"^WEBHOOK_SECRET_KEYS entries must be key_id:base64key$"),
        ("k1:not base64!", r"^WEBHOOK_SECRET_KEYS contains an invalid base64 key$"),
        (
            f"k1:{base64.b64encode(b'short').decode()}",
            r"^WEBHOOK_SECRET_KEYS keys must be 32 bytes \(AES-256\)$",
        ),
    ],
)
def test_the_webhook_keyring_errors_name_the_webhook_variable(value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message) as caught:
        parse_webhook_keyring(value)
    assert "PII_MAPPING_KEYS" not in str(caught.value)


def test_the_webhook_keyring_parses_like_the_pii_keyring() -> None:
    value = f"k1:{KEY_ONE}, k2:{KEY_TWO}"
    assert parse_webhook_keyring(value) == parse_keyring(value)
    assert parse_webhook_keyring(f"k1:{KEY_ONE}") == {"k1": base64.b64decode(KEY_ONE)}


def test_webhook_settings_defaults() -> None:
    settings = _settings()

    assert settings.webhook_secret_keys == ""
    assert settings.webhook_secret_active_key_id == ""
    assert settings.webhook_delivery_timeout_seconds == 10
    assert settings.webhook_sweep_interval_seconds == 30
    assert settings.webhook_max_endpoints_per_workspace == 20


def test_webhook_secret_keys_is_a_hidden_secret_setting() -> None:
    settings = _settings(
        webhook_secret_keys=f"k1:{KEY_ONE}",
        webhook_secret_active_key_id="k1",  # noqa: S106 - a key id
    )

    assert "webhook_secret_keys" in SECRET_FIELDS
    assert KEY_ONE not in repr(settings)
    assert KEY_ONE not in str(settings)
    assert settings.model_dump()["webhook_secret_keys"] != settings.webhook_secret_keys


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"webhook_secret_keys": "no-colon"}, "WEBHOOK_SECRET_KEYS entries"),
        ({"webhook_secret_keys": "k1:not base64!"}, "WEBHOOK_SECRET_KEYS contains"),
        (
            {"webhook_secret_keys": f"k1:{KEY_ONE}", "webhook_secret_active_key_id": "k2"},
            "WEBHOOK_SECRET_ACTIVE_KEY_ID must name one of WEBHOOK_SECRET_KEYS",
        ),
        (
            {"webhook_secret_active_key_id": "k1"},
            "WEBHOOK_SECRET_ACTIVE_KEY_ID must name one of WEBHOOK_SECRET_KEYS",
        ),
        ({"webhook_delivery_timeout_seconds": 0}, "WEBHOOK_DELIVERY_TIMEOUT_SECONDS"),
        ({"webhook_sweep_interval_seconds": 0}, "WEBHOOK_SWEEP_INTERVAL_SECONDS"),
        ({"webhook_max_endpoints_per_workspace": 0}, "WEBHOOK_MAX_ENDPOINTS_PER_WORKSPACE"),
    ],
)
def test_invalid_webhook_settings_are_refused_without_the_key(overrides, message) -> None:
    with pytest.raises(ValidationError) as caught:
        _settings(**overrides)

    assert message in str(caught.value)
    assert KEY_ONE not in str(caught.value)
