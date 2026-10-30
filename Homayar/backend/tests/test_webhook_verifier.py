"""HOM-89: the receiver side in the Python SDK (DESIGN section 8).

``verify_webhook`` checks a request in a fixed order and names the first check
that fails. ``WebhookDeduplicator`` drops a second delivery of the same event.
The signatures here are computed by hand, not with the backend signer, so a
mistake in the signer cannot hide the same mistake in the verifier.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import re
from pathlib import Path

import httpx
import pytest
from homayar_client import WebhookDeduplicator, WebhookVerificationError, verify_webhook
from starlette.datastructures import Headers

SDK_ROOT = Path(__file__).resolve().parents[2] / "sdk" / "python"
NOW = 1_790_000_000
EVENT_ID = "6c0f4c1a-1b2d-4e3f-8a9b-0c1d2e3f4a5b"
KEY_ID = "whk_" + "1" * 24
OLD_KEY_ID = "whk_" + "2" * 24
SECRET = "whsec_" + "A" * 43
OLD_SECRET = "whsec_" + "B" * 43
BODY = json.dumps(
    {"created_at": "2026-10-30T12:00:00Z", "data": {}, "id": EVENT_ID, "type": "webhook.test"},
    separators=(",", ":"),
    sort_keys=True,
).encode()


def _hex(secret: str, event_id: str, t: str, body: bytes) -> str:
    # UTF-8 equals ASCII for real headers; it also lets a test sign a malformed one.
    content = f"{event_id}.{t}.".encode() + body
    return hmac.new(secret.encode("utf-8"), content, hashlib.sha256).hexdigest()


def _headers(
    *,
    event_id: str = EVENT_ID,
    t: str = str(NOW),
    body: bytes = BODY,
    keys: tuple[tuple[str, str], ...] = ((KEY_ID, SECRET),),
    signature: str | None = None,
) -> dict[str, str]:
    if signature is None:
        signature = ",".join(f"v1={key}:{_hex(secret, event_id, t, body)}" for key, secret in keys)
    return {
        "Homayar-Webhook-Id": event_id,
        "Homayar-Webhook-Timestamp": t,
        "Homayar-Webhook-Signature": signature,
    }


def _code(headers, body: bytes = BODY, secrets=None, now: float = NOW) -> str:
    with pytest.raises(WebhookVerificationError) as caught:
        verify_webhook(headers=headers, body=body, secrets=secrets or {KEY_ID: SECRET}, now=now)
    return caught.value.code


# --- the happy path and header handling -----------------------------------------------


def test_a_valid_request_returns_the_event() -> None:
    event = verify_webhook(headers=_headers(), body=BODY, secrets={KEY_ID: SECRET}, now=NOW)

    assert event == json.loads(BODY)


@pytest.mark.parametrize("case", ["lower", "upper", "mixed"])
def test_header_names_match_in_any_case(case: str) -> None:
    change = {"lower": str.lower, "upper": str.upper, "mixed": str.swapcase}[case]
    headers = {change(name): value for name, value in _headers().items()}

    assert verify_webhook(headers=headers, body=BODY, secrets={KEY_ID: SECRET}, now=NOW)


@pytest.mark.parametrize("kind", [httpx.Headers, lambda raw: Headers(headers=raw)])
def test_framework_header_types_are_accepted(kind) -> None:
    headers = kind({name.lower(): value for name, value in _headers().items()})

    assert verify_webhook(headers=headers, body=BODY, secrets={KEY_ID: SECRET}, now=NOW)


@pytest.mark.parametrize(
    "name",
    ["Homayar-Webhook-Id", "Homayar-Webhook-Timestamp", "Homayar-Webhook-Signature"],
)
def test_an_absent_header_is_missing_header(name: str) -> None:
    headers = _headers()
    del headers[name]

    assert _code(headers) == "missing_header"


@pytest.mark.parametrize(
    "name",
    ["Homayar-Webhook-Id", "Homayar-Webhook-Timestamp", "Homayar-Webhook-Signature"],
)
def test_an_empty_header_is_missing_header(name: str) -> None:
    headers = _headers()
    headers[name] = ""

    assert _code(headers) == "missing_header"


# --- timestamp ------------------------------------------------------------------------


@pytest.mark.parametrize("t", ["17e8", "-5", "1.5", "+5", " 5x", "١٧٩٠٠٠٠٠٠٠", "0x10"])
def test_a_timestamp_that_is_not_ascii_digits_is_malformed(t: str) -> None:
    assert _code(_headers(t=t)) == "malformed_header"


@pytest.mark.parametrize("offset", [-300, 300, 0])
def test_a_timestamp_inside_the_tolerance_is_accepted(offset: int) -> None:
    headers = _headers(t=str(NOW + offset))

    assert verify_webhook(headers=headers, body=BODY, secrets={KEY_ID: SECRET}, now=NOW)


@pytest.mark.parametrize("offset", [-301, 301])
def test_a_timestamp_outside_the_tolerance_is_refused(offset: int) -> None:
    assert _code(_headers(t=str(NOW + offset))) == "timestamp_out_of_tolerance"


def test_the_tolerance_can_be_changed() -> None:
    headers = _headers(t=str(NOW - 60))

    with pytest.raises(WebhookVerificationError) as caught:
        verify_webhook(
            headers=headers, body=BODY, secrets={KEY_ID: SECRET}, now=NOW, tolerance_seconds=30
        )
    assert caught.value.code == "timestamp_out_of_tolerance"


def test_a_huge_digit_timestamp_is_out_of_tolerance_not_a_crash() -> None:
    assert _code(_headers(t="9" * 5000)) == "timestamp_out_of_tolerance"


def test_now_defaults_to_the_clock() -> None:
    with pytest.raises(WebhookVerificationError) as caught:
        verify_webhook(headers=_headers(t="1000"), body=BODY, secrets={KEY_ID: SECRET})
    assert caught.value.code == "timestamp_out_of_tolerance"


# --- signature header -----------------------------------------------------------------


@pytest.mark.parametrize(
    "signature",
    [
        "v2=" + KEY_ID + ":" + "a" * 64,
        "v1=" + KEY_ID + ":" + "A" * 64,
        "v1=" + KEY_ID + ":" + "a" * 63,
        "v1=" + "a" * 64,
        "v1=:" + "a" * 64,
        "garbage",
        ",,,",
    ],
)
def test_a_signature_header_without_a_well_formed_v1_entry_is_malformed(signature: str) -> None:
    assert _code(_headers(signature=signature)) == "malformed_header"


def test_entries_of_other_versions_are_ignored() -> None:
    good = _headers()["Homayar-Webhook-Signature"]
    headers = _headers(signature=f"v2={KEY_ID}:{'f' * 64}, {good}")

    assert verify_webhook(headers=headers, body=BODY, secrets={KEY_ID: SECRET}, now=NOW)


def test_entries_only_for_unknown_key_ids_are_unknown_key() -> None:
    headers = _headers(keys=((OLD_KEY_ID, OLD_SECRET),))

    assert _code(headers) == "unknown_key"


def test_a_known_key_id_with_a_wrong_hex_is_invalid_signature() -> None:
    headers = _headers(signature=f"v1={KEY_ID}:{'0' * 64}")

    assert _code(headers) == "invalid_signature"


def test_one_changed_body_byte_is_invalid_signature() -> None:
    changed = BODY.replace(b'"type"', b'"tYpe"')

    assert _code(_headers(), body=changed) == "invalid_signature"


def test_a_changed_id_header_is_invalid_signature() -> None:
    headers = _headers()
    headers["Homayar-Webhook-Id"] = "7c0f4c1a-1b2d-4e3f-8a9b-0c1d2e3f4a5b"

    assert _code(headers) == "invalid_signature"


def test_a_changed_timestamp_is_invalid_signature() -> None:
    headers = _headers()
    headers["Homayar-Webhook-Timestamp"] = str(NOW + 1)

    assert _code(headers) == "invalid_signature"


def test_the_order_of_checks_is_fixed() -> None:
    stale_and_unknown = _headers(t=str(NOW - 1000), keys=((OLD_KEY_ID, OLD_SECRET),))
    malformed_and_stale = _headers(t="-1000", signature="v2=x")

    assert _code(stale_and_unknown) == "timestamp_out_of_tolerance"
    assert _code(malformed_and_stale) == "malformed_header"


# --- rotation -------------------------------------------------------------------------


def test_during_rotation_either_secret_alone_verifies() -> None:
    headers = _headers(keys=((KEY_ID, SECRET), (OLD_KEY_ID, OLD_SECRET)))

    assert verify_webhook(headers=headers, body=BODY, secrets={KEY_ID: SECRET}, now=NOW)
    assert verify_webhook(headers=headers, body=BODY, secrets={OLD_KEY_ID: OLD_SECRET}, now=NOW)


def test_after_rotation_the_old_secret_alone_is_unknown_key() -> None:
    headers = _headers(keys=((KEY_ID, SECRET),))

    assert _code(headers, secrets={OLD_KEY_ID: OLD_SECRET}) == "unknown_key"


def test_a_right_key_wins_over_a_wrong_entry_for_another_known_key() -> None:
    good = _headers()["Homayar-Webhook-Signature"]
    headers = _headers(signature=f"v1={OLD_KEY_ID}:{'0' * 64},{good}")

    secrets = {KEY_ID: SECRET, OLD_KEY_ID: OLD_SECRET}
    assert verify_webhook(headers=headers, body=BODY, secrets=secrets, now=NOW)


# --- payload --------------------------------------------------------------------------


def test_a_signed_json_list_is_invalid_payload() -> None:
    body = b"[1,2]"

    assert _code(_headers(body=body), body=body) == "invalid_payload"


def test_a_signed_body_whose_id_differs_from_the_header_is_invalid_payload() -> None:
    body = BODY.replace(EVENT_ID.encode(), b"other-id")

    assert _code(_headers(body=body), body=body) == "invalid_payload"


@pytest.mark.parametrize("body", [b"not json", b"\xff\xfe", b""])
def test_a_signed_body_that_is_not_json_is_invalid_payload(body: bytes) -> None:
    assert _code(_headers(body=body), body=body) == "invalid_payload"


def test_a_decoded_string_body_is_refused() -> None:
    with pytest.raises(TypeError):
        verify_webhook(headers=_headers(), body=BODY.decode(), secrets={KEY_ID: SECRET}, now=NOW)


def test_the_error_names_its_code_and_never_the_secret() -> None:
    with pytest.raises(WebhookVerificationError) as caught:
        verify_webhook(
            headers=_headers(signature=f"v1={KEY_ID}:{'0' * 64}"),
            body=BODY,
            secrets={KEY_ID: SECRET},
            now=NOW,
        )

    assert caught.value.code == "invalid_signature"
    assert "invalid_signature" in str(caught.value)
    assert SECRET not in str(caught.value) and SECRET not in repr(caught.value)


# --- deduplication --------------------------------------------------------------------


def test_the_deduplicator_reports_a_second_delivery() -> None:
    dedupe = WebhookDeduplicator(ttl_seconds=60)

    assert dedupe.seen_before("e1", now=1000) is False
    assert dedupe.seen_before("e1", now=1001) is True
    assert dedupe.seen_before("e2", now=1001) is False
    assert dedupe.seen_before("e1", now=1060) is True


def test_the_deduplicator_forgets_after_the_ttl() -> None:
    dedupe = WebhookDeduplicator(ttl_seconds=60)
    dedupe.seen_before("e1", now=1000)

    assert dedupe.seen_before("e1", now=1061) is False
    assert dedupe.seen_before("e1", now=1062) is True


def test_the_deduplicator_default_ttl_is_seven_days() -> None:
    dedupe = WebhookDeduplicator()
    dedupe.seen_before("e1", now=0)

    assert dedupe.seen_before("e1", now=7 * 24 * 3600) is True
    assert dedupe.seen_before("e1", now=7 * 24 * 3600 + 1) is False


def test_the_deduplicator_does_not_grow_without_bound() -> None:
    dedupe = WebhookDeduplicator(ttl_seconds=10)
    for second in range(1000):
        dedupe.seen_before(f"e{second}", now=second)

    assert len(dedupe) <= 11


def test_the_deduplicator_needs_a_positive_ttl() -> None:
    with pytest.raises(ValueError):
        WebhookDeduplicator(ttl_seconds=0)


# --- packaging and the example receiver -----------------------------------------------


def test_the_verifier_and_example_use_only_the_standard_library() -> None:
    for source in (
        SDK_ROOT / "homayar_client" / "webhooks.py",
        SDK_ROOT / "examples" / "verify_webhook.py",
    ):
        text = source.read_text(encoding="utf-8")
        imported = re.findall(r"^\s*(?:from|import)\s+([A-Za-z_][\w]*)", text, re.MULTILINE)
        assert "backend" not in imported, source
        assert "httpx" not in imported, source


def _example():
    spec = importlib.util.spec_from_file_location(
        "verify_webhook_example", SDK_ROOT / "examples" / "verify_webhook.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_importing_the_example_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    import http.server

    def refuse(*args, **kwargs):
        raise AssertionError("the example started a server on import")

    monkeypatch.setattr(http.server.HTTPServer, "__init__", refuse)
    monkeypatch.setattr(http.server.ThreadingHTTPServer, "__init__", refuse)

    assert callable(_example().main)


def test_the_example_verifies_then_deduplicates() -> None:
    example = _example()
    secrets = example.parse_secrets(f"{KEY_ID}:{SECRET}, {OLD_KEY_ID}:{OLD_SECRET}")
    dedupe = WebhookDeduplicator()

    first = example.handle(_headers(), BODY, secrets=secrets, deduplicator=dedupe, now=NOW)
    second = example.handle(_headers(), BODY, secrets=secrets, deduplicator=dedupe, now=NOW)
    forged = example.handle(
        _headers(signature=f"v1={KEY_ID}:{'0' * 64}"),
        BODY,
        secrets=secrets,
        deduplicator=dedupe,
        now=NOW,
    )

    assert secrets == {KEY_ID: SECRET, OLD_KEY_ID: OLD_SECRET}
    assert first[0] == 200 and first[1]["status"] == "processed"
    assert second[0] == 200 and second[1]["status"] == "duplicate"
    assert forged == (400, {"error": "invalid_signature"})
