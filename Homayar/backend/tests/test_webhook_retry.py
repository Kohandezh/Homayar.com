"""HOM-90: the retry schedule and the dead letter (ADR 0050 D9).

Every request goes through ``httpx.MockTransport``; nothing reaches the network.
``now`` is pinned, so every ``next_attempt_at`` is exact (there is no jitter).
"""

from __future__ import annotations

import logging
from datetime import timedelta

import httpx
import pytest
from homayar_client import verify_webhook
from pydantic import ValidationError

from backend.config import Settings
from backend.database.saas_models import WebhookDelivery, WebhookEndpoint
from backend.jobs import webhook_delivery
from backend.jobs.webhook_delivery import deliver_webhook, due_delivery_ids
from backend.tests import test_webhook_delivery as _delivery_tests
from backend.tests.test_webhook_api import ENDPOINTS, _created, _owner
from backend.tests.test_webhook_delivery import (
    Receiver,
    WorkerStopped,
    _attempts,
    _delivery,
    _enqueue,
    _one_delivery,
    _refused,
    _timeout,
    _update,
    _utc,
)

# The worker fixtures are discovered from this module namespace.
settings = _delivery_tests.settings
worker_settings = _delivery_tests.worker_settings
no_publish = _delivery_tests.no_publish
now = _delivery_tests.now

DEFAULT_DELAYS = (60, 300, 1800, 7200, 18000, 36000, 36000)
LONG_AFTER = timedelta(days=365)


def _never_due_again(delivery_id: str, at) -> None:
    later = at + LONG_AFTER
    assert delivery_id not in due_delivery_ids(now=later, limit=100)
    receiver = Receiver()
    before = _delivery(delivery_id)
    deliver_webhook(delivery_id, now=later, http_client=receiver.client)
    assert receiver.requests == []
    assert _delivery(delivery_id) == before


def _fail_until_dead(delivery_id: str, start, delays: list[int], answer) -> list:
    """Fail every attempt at the time it is due; returns the times of the attempts."""
    at, times = start, []
    for delay in delays:
        times.append(at)
        deliver_webhook(delivery_id, now=at, http_client=Receiver(answer).client)
        row = _delivery(delivery_id)
        assert row["state"] == "pending"
        assert _utc(row["next_attempt_at"]) == at + timedelta(seconds=delay)
        at = at + timedelta(seconds=delay)
    times.append(at)
    deliver_webhook(delivery_id, now=at, http_client=Receiver(answer).client)
    return times


# --- settings (A10) ---------------------------------------------------------------------


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_retry_settings_defaults():
    settings = _settings()

    assert settings.webhook_max_attempts == 8
    assert settings.webhook_retry_delays_seconds == "60,300,1800,7200,18000,36000,36000"
    assert settings.webhook_retry_delays == DEFAULT_DELAYS
    # About 27 hours from the first attempt to the last.
    assert 27 * 3600 <= sum(DEFAULT_DELAYS) < 28 * 3600


@pytest.mark.parametrize(
    ("value", "parsed"),
    [
        ("10,20", (10, 20)),
        (" 60 , 300 ", (60, 300)),
        ("5", (5,)),
        ("1,604800", (1, 604800)),
    ],
)
def test_a_valid_delay_list_is_parsed(value, parsed):
    assert _settings(webhook_retry_delays_seconds=value).webhook_retry_delays == parsed


@pytest.mark.parametrize("attempts", [1, 20])
def test_the_attempt_limits_are_accepted(attempts):
    assert _settings(webhook_max_attempts=attempts).webhook_max_attempts == attempts


@pytest.mark.parametrize("attempts", [0, 21, -1])
def test_an_attempt_count_out_of_range_refuses_to_start(attempts):
    with pytest.raises(ValueError, match="WEBHOOK_MAX_ATTEMPTS"):
        _settings(webhook_max_attempts=attempts)


@pytest.mark.parametrize(
    "value",
    ["abc", "", " ", "0", "604801", "60,,300", "60,", ",60", "-5", "1.5", "1_0", "١٠", "60;300"],
)
def test_a_bad_delay_list_refuses_to_start(value):
    with pytest.raises(ValueError, match="WEBHOOK_RETRY_DELAYS_SECONDS"):
        _settings(webhook_retry_delays_seconds=value)


def test_the_retry_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("WEBHOOK_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("WEBHOOK_RETRY_DELAYS_SECONDS", "10, 20")
    settings = _settings()
    assert (settings.webhook_max_attempts, settings.webhook_retry_delays) == (4, (10, 20))

    monkeypatch.setenv("WEBHOOK_MAX_ATTEMPTS", "abc")
    with pytest.raises(ValidationError):
        _settings()


# --- one failed attempt is retried (A1, A2, A5, A6) -------------------------------------


def test_a_500_schedules_the_next_attempt_after_the_first_delay(
    client, email_outbox, monkeypatch, now
):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)

    deliver_webhook(delivery_id, now=now, http_client=Receiver(httpx.Response(500)).client)

    row = _delivery(delivery_id)
    assert row["state"] == "pending"
    assert row["attempt_count"] == 1
    assert _utc(row["next_attempt_at"]) == now + timedelta(seconds=60)
    assert (row["last_status_code"], row["last_error"]) == (500, "http_status")
    assert row["delivered_at"] is None
    [attempt] = _attempts(delivery_id)
    assert (attempt["attempt_number"], attempt["outcome"], attempt["status_code"]) == (
        1,
        "failed",
        500,
    )


def test_a_retry_waits_for_its_time_and_resends_the_same_bytes(
    client, email_outbox, monkeypatch, now
):
    _, created, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    receiver = Receiver(httpx.Response(500), httpx.Response(204))
    deliver_webhook(delivery_id, now=now, http_client=receiver.client)
    first_attempt = _attempts(delivery_id)[0]
    retry_at = now + timedelta(seconds=60)

    # Deny: one second before its time it is not due and nothing is sent.
    early = retry_at - timedelta(seconds=1)
    assert delivery_id not in due_delivery_ids(now=early, limit=100)
    before = _delivery(delivery_id)
    deliver_webhook(delivery_id, now=early, http_client=receiver.client)
    assert len(receiver.requests) == 1
    assert _delivery(delivery_id) == before

    # Allow: at exactly its time it is due and sent again.
    assert delivery_id in due_delivery_ids(now=retry_at, limit=100)
    deliver_webhook(delivery_id, now=retry_at, http_client=receiver.client)

    first, second = receiver.requests
    assert second.content == first.content == before["payload"]
    assert second.headers["Homayar-Webhook-Id"] == first.headers["Homayar-Webhook-Id"]
    secrets = {created["key_id"]: created["signing_secret"]}
    for request, at in ((first, now), (second, retry_at)):
        assert request.headers["Homayar-Webhook-Timestamp"] == str(int(at.timestamp()))
        verify_webhook(
            headers=request.headers, body=request.content, secrets=secrets, now=at.timestamp()
        )

    # A5: the success ends the delivery; the first attempt row is unchanged.
    row = _delivery(delivery_id)
    assert (row["state"], row["attempt_count"], row["next_attempt_at"]) == ("delivered", 2, None)
    assert _utc(row["delivered_at"]) == retry_at
    assert (row["last_status_code"], row["last_error"]) == (204, None)
    attempts = _attempts(delivery_id)
    assert attempts[0] == first_attempt
    assert (attempts[1]["attempt_number"], attempts[1]["outcome"]) == (2, "delivered")


@pytest.mark.parametrize(
    ("answer", "status", "code"),
    [
        (httpx.Response(503), 503, "http_status"),
        (httpx.Response(404), 404, "http_status"),
        (httpx.Response(400), 400, "http_status"),
        (
            httpx.Response(302, headers={"Location": "https://other.example.com/"}),
            302,
            "http_status",
        ),
        (_timeout, None, "timeout"),
        (_refused, None, "connection_error"),
    ],
    ids=["5xx", "404", "400", "3xx", "timeout", "connection error"],
)
def test_every_kind_of_failed_attempt_is_retried(
    client, email_outbox, monkeypatch, now, answer, status, code
):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)

    deliver_webhook(delivery_id, now=now, http_client=Receiver(answer).client)

    row = _delivery(delivery_id)
    assert (row["state"], row["last_status_code"], row["last_error"]) == ("pending", status, code)
    assert _utc(row["next_attempt_at"]) == now + timedelta(seconds=60)
    [attempt] = _attempts(delivery_id)
    assert (attempt["outcome"], attempt["status_code"], attempt["error_code"]) == (
        "failed",
        status,
        code,
    )

    retry_at = now + timedelta(seconds=60)
    receiver = Receiver()
    deliver_webhook(delivery_id, now=retry_at, http_client=receiver.client)
    assert len(receiver.requests) == 1
    assert _delivery(delivery_id)["state"] == "delivered"


# --- the schedule and the dead letter (A3, A4) ------------------------------------------


def test_the_default_schedule_ends_in_the_dead_letter(client, email_outbox, monkeypatch, now):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)

    times = _fail_until_dead(delivery_id, now, list(DEFAULT_DELAYS), httpx.Response(500))

    row = _delivery(delivery_id)
    assert (row["state"], row["next_attempt_at"], row["attempt_count"]) == ("dead_letter", None, 8)
    assert (row["last_status_code"], row["last_error"]) == (500, "http_status")
    assert row["delivered_at"] is None
    attempts = _attempts(delivery_id)
    assert [attempt["attempt_number"] for attempt in attempts] == list(range(1, 9))
    assert [_utc(attempt["started_at"]) for attempt in attempts] == times
    _never_due_again(delivery_id, times[-1])


def test_a_short_delay_list_repeats_its_last_delay(
    client, email_outbox, monkeypatch, now, settings
):
    monkeypatch.setattr(settings, "webhook_max_attempts", 4)
    monkeypatch.setattr(settings, "webhook_retry_delays_seconds", "10,20")
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)

    times = _fail_until_dead(delivery_id, now, [10, 20, 20], _timeout)

    assert times == [now + timedelta(seconds=s) for s in (0, 10, 30, 50)]
    row = _delivery(delivery_id)
    assert (row["state"], row["next_attempt_at"], row["attempt_count"]) == ("dead_letter", None, 4)
    assert (row["last_status_code"], row["last_error"]) == (None, "timeout")
    assert len(_attempts(delivery_id)) == 4
    _never_due_again(delivery_id, times[-1])


def test_the_last_attempt_of_the_budget_is_sent(client, email_outbox, monkeypatch, now, settings):
    """Allow control for the dead letter: count = max - 1 is still claimed and sent."""
    monkeypatch.setattr(settings, "webhook_max_attempts", 3)
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _update(WebhookDelivery, delivery_id, attempt_count=2, next_attempt_at=now)
    receiver = Receiver()

    deliver_webhook(delivery_id, now=now, http_client=receiver.client)

    assert len(receiver.requests) == 1
    row = _delivery(delivery_id)
    assert (row["state"], row["attempt_count"]) == ("delivered", 3)


def test_with_one_attempt_the_first_failure_is_the_dead_letter(
    client, email_outbox, monkeypatch, now, settings
):
    monkeypatch.setattr(settings, "webhook_max_attempts", 1)
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)

    deliver_webhook(delivery_id, now=now, http_client=Receiver(httpx.Response(500)).client)

    row = _delivery(delivery_id)
    assert (row["state"], row["next_attempt_at"], row["attempt_count"]) == ("dead_letter", None, 1)
    _never_due_again(delivery_id, now)


def test_the_retry_settings_are_read_at_call_time(client, email_outbox, monkeypatch, now, settings):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    monkeypatch.setattr(settings, "webhook_retry_delays_seconds", "7")

    deliver_webhook(delivery_id, now=now, http_client=Receiver(httpx.Response(500)).client)

    assert _utc(_delivery(delivery_id)["next_attempt_at"]) == now + timedelta(seconds=7)


# --- refusals go to the dead letter (A7) ------------------------------------------------


def _deactivate(owner, created, delivery_id) -> None:
    patched = owner.patch(f"{ENDPOINTS}/{created['endpoint']['id']}", json={"is_active": False})
    assert patched.status_code == 200


def _unsafe_url(owner, created, delivery_id) -> None:
    _update(WebhookEndpoint, created["endpoint"]["id"], url="http://169.254.169.254/latest")


def _no_payload(owner, created, delivery_id) -> None:
    _update(WebhookDelivery, delivery_id, payload=None)


def _no_keyring(owner, created, delivery_id, *, monkeypatch, settings) -> None:
    monkeypatch.setattr(settings, "webhook_secret_keys", "")
    monkeypatch.setattr(settings, "webhook_secret_active_key_id", "")


@pytest.mark.parametrize(
    ("cause", "code"),
    [
        (_deactivate, "endpoint_inactive"),
        (_unsafe_url, "url_refused"),
        (_no_payload, "payload_missing"),
        (_no_keyring, "signing_unavailable"),
    ],
    ids=["endpoint_inactive", "url_refused", "payload_missing", "signing_unavailable"],
)
@pytest.mark.parametrize("earlier_failures", [0, 2])
def test_a_refusal_moves_the_row_to_the_dead_letter(
    client, email_outbox, monkeypatch, now, settings, caplog, cause, code, earlier_failures
):
    caplog.set_level(logging.INFO)
    owner, created, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    at = now
    for _ in range(earlier_failures):  # a retry can be refused too
        deliver_webhook(delivery_id, now=at, http_client=Receiver(httpx.Response(500)).client)
        at = _utc(_delivery(delivery_id)["next_attempt_at"])
    if cause is _no_keyring:
        cause(owner, created, delivery_id, monkeypatch=monkeypatch, settings=settings)
    else:
        cause(owner, created, delivery_id)
    receiver = Receiver()

    deliver_webhook(delivery_id, now=at, http_client=receiver.client)

    assert receiver.requests == []
    row = _delivery(delivery_id)
    assert (row["state"], row["last_error"], row["next_attempt_at"]) == ("dead_letter", code, None)
    assert row["attempt_count"] == earlier_failures
    assert len(_attempts(delivery_id)) == earlier_failures
    [line] = [
        record for record in caplog.records if "WEBHOOK_DELIVERY_DEAD_LETTER" in record.getMessage()
    ]
    assert line.levelno == logging.WARNING
    assert line.getMessage() == (
        f"WEBHOOK_DELIVERY_DEAD_LETTER delivery_id={delivery_id} "
        f"endpoint_id={created['endpoint']['id']} attempts={earlier_failures} error_code={code}"
    )
    _never_due_again(delivery_id, at)


def test_the_same_retry_with_its_cause_fixed_is_sent(client, email_outbox, monkeypatch, now):
    """Allow control for the refusals: an active endpoint with a usable key sends."""
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    deliver_webhook(delivery_id, now=now, http_client=Receiver(httpx.Response(500)).client)
    receiver = Receiver()

    deliver_webhook(delivery_id, now=now + timedelta(seconds=60), http_client=receiver.client)

    assert len(receiver.requests) == 1
    assert _delivery(delivery_id)["state"] == "delivered"


# --- an expired lease (A8) --------------------------------------------------------------


def _stop_after_sending(request: httpx.Request) -> httpx.Response:
    raise WorkerStopped


def _lease_end(settings, at):
    return at + timedelta(seconds=settings.webhook_delivery_timeout_seconds + 30)


def test_an_expired_lease_with_budget_left_is_sent_again(
    client, email_outbox, monkeypatch, now, settings
):
    monkeypatch.setattr(settings, "webhook_max_attempts", 2)
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    with pytest.raises(WorkerStopped):
        deliver_webhook(delivery_id, now=now, http_client=Receiver(_stop_after_sending).client)
    receiver = Receiver()

    deliver_webhook(delivery_id, now=_lease_end(settings, now), http_client=receiver.client)

    assert len(receiver.requests) == 1
    row = _delivery(delivery_id)
    assert (row["state"], row["attempt_count"]) == ("delivered", 2)


@pytest.mark.parametrize("endpoint_inactive", [False, True])
def test_an_expired_lease_without_budget_goes_to_the_dead_letter_unsent(
    client, email_outbox, monkeypatch, now, settings, caplog, endpoint_inactive
):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(settings, "webhook_max_attempts", 2)
    owner, created, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    deliver_webhook(delivery_id, now=now, http_client=Receiver(httpx.Response(500)).client)
    retry_at = now + timedelta(seconds=60)
    with pytest.raises(WorkerStopped):
        deliver_webhook(delivery_id, now=retry_at, http_client=Receiver(_stop_after_sending).client)
    lease_end = _lease_end(settings, retry_at)
    if endpoint_inactive:  # the budget check runs before the refusals
        _deactivate(owner, created, delivery_id)
    receiver = Receiver()

    # Before the lease ends nothing happens.
    deliver_webhook(delivery_id, now=lease_end - timedelta(seconds=1), http_client=receiver.client)
    assert _delivery(delivery_id)["state"] == "sending"

    deliver_webhook(delivery_id, now=lease_end, http_client=receiver.client)

    assert receiver.requests == []
    row = _delivery(delivery_id)
    assert (row["state"], row["last_error"], row["next_attempt_at"]) == (
        "dead_letter",
        "lease_expired",
        None,
    )
    assert row["attempt_count"] == 2
    assert len(_attempts(delivery_id)) == 1  # the lost request left no attempt row
    [line] = [
        record for record in caplog.records if "WEBHOOK_DELIVERY_DEAD_LETTER" in record.getMessage()
    ]
    assert line.getMessage() == (
        f"WEBHOOK_DELIVERY_DEAD_LETTER delivery_id={delivery_id} "
        f"endpoint_id={created['endpoint']['id']} attempts=2 error_code=lease_expired"
    )
    _never_due_again(delivery_id, lease_end)


def test_a_late_record_after_the_lease_dead_letter_changes_nothing(
    client, email_outbox, monkeypatch, now, settings
):
    """The stopped worker's request may still end; its record must not revive the row."""
    monkeypatch.setattr(settings, "webhook_max_attempts", 1)
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    claim = webhook_delivery._claim(
        delivery_id, _delivery(delivery_id)["workspace_id"], now=now, settings=settings
    )
    assert claim is not None
    lease_end = _lease_end(settings, now)
    deliver_webhook(delivery_id, now=lease_end, http_client=Receiver().client)
    assert _delivery(delivery_id)["state"] == "dead_letter"

    result = webhook_delivery._Result("failed", 500, "http_status", 5)
    webhook_delivery._record(
        claim, result, timestamp=int(now.timestamp()), now=now, settings=settings
    )

    row = _delivery(delivery_id)
    assert (row["state"], row["last_error"]) == ("dead_letter", "lease_expired")
    assert len(_attempts(delivery_id)) == 1  # the request that left is still on the ledger


# --- rows past the budget (A9) ----------------------------------------------------------


def _redeliver(delivery_id: str, at) -> None:
    """What team t2's admin redelivery does: back to pending, due now, count kept."""
    _update(WebhookDelivery, delivery_id, state="pending", next_attempt_at=at)


def _exhaust(client, email_outbox, monkeypatch, now, settings):
    monkeypatch.setattr(settings, "webhook_max_attempts", 2)
    monkeypatch.setattr(settings, "webhook_retry_delays_seconds", "10")
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _fail_until_dead(delivery_id, now, [10], httpx.Response(500))
    assert _delivery(delivery_id)["state"] == "dead_letter"
    return delivery_id, now + timedelta(hours=1)


def test_a_redelivered_exhausted_row_that_fails_again_is_dead_at_once(
    client, email_outbox, monkeypatch, now, settings
):
    delivery_id, at = _exhaust(client, email_outbox, monkeypatch, now, settings)
    _redeliver(delivery_id, at)
    receiver = Receiver(httpx.Response(502))

    deliver_webhook(delivery_id, now=at, http_client=receiver.client)

    assert len(receiver.requests) == 1
    row = _delivery(delivery_id)
    assert (row["state"], row["next_attempt_at"], row["attempt_count"]) == ("dead_letter", None, 3)
    assert (row["last_status_code"], row["last_error"]) == (502, "http_status")
    assert len(_attempts(delivery_id)) == 3
    _never_due_again(delivery_id, at)


def test_a_redelivered_exhausted_row_that_succeeds_is_delivered(
    client, email_outbox, monkeypatch, now, settings
):
    delivery_id, at = _exhaust(client, email_outbox, monkeypatch, now, settings)
    _redeliver(delivery_id, at)
    receiver = Receiver()

    deliver_webhook(delivery_id, now=at, http_client=receiver.client)

    assert len(receiver.requests) == 1
    row = _delivery(delivery_id)
    assert (row["state"], row["attempt_count"]) == ("delivered", 3)
    assert _utc(row["delivered_at"]) == at


def test_a_redelivered_row_with_budget_left_gets_the_rest_of_the_schedule(
    client, email_outbox, monkeypatch, now
):
    _, _, delivery_id = _one_delivery(client, email_outbox, monkeypatch, now)
    _update(
        WebhookDelivery, delivery_id, state="dead_letter", attempt_count=2, next_attempt_at=None
    )
    _redeliver(delivery_id, now)

    deliver_webhook(delivery_id, now=now, http_client=Receiver(httpx.Response(500)).client)

    row = _delivery(delivery_id)
    assert (row["state"], row["attempt_count"]) == ("pending", 3)
    assert _utc(row["next_attempt_at"]) == now + timedelta(seconds=DEFAULT_DELAYS[2])


# --- nothing secret on the ledger or in the logs (A11) ----------------------------------


def test_retries_and_the_dead_letter_carry_no_secret_path_query_or_payload(
    client, email_outbox, monkeypatch, now, settings, caplog
):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(settings, "webhook_max_attempts", 3)
    owner = _owner(client, email_outbox, monkeypatch)
    created = _created(
        owner, "https://hooks.example.com/path-secret-value/in?token=query-secret-value"
    )
    [delivery_id] = _enqueue(owner.workspace_id, now=now, data={"marker": "payload-sentinel"})
    receiver = Receiver(httpx.Response(500), _timeout, httpx.Response(404))

    at = now
    for _ in range(3):
        deliver_webhook(delivery_id, now=at, http_client=receiver.client)
        row = _delivery(delivery_id)
        if row["state"] == "pending":
            at = _utc(row["next_attempt_at"])

    assert row["state"] == "dead_letter"
    assert len(receiver.requests) == 3
    signatures = [
        entry.split(":")[1]
        for request in receiver.requests
        for entry in request.headers["Homayar-Webhook-Signature"].split(",")
    ]
    forbidden = (
        created["signing_secret"],
        "path-secret-value",
        "query-secret-value",
        "payload-sentinel",
        *signatures,
    )
    logged = "\n".join(f"{record.getMessage()} {record.args!r}" for record in caplog.records)
    ledger = repr(_attempts(delivery_id)) + repr(
        {key: value for key, value in row.items() if key != "payload"}
    )
    assert "WEBHOOK_DELIVERY_DEAD_LETTER" in logged
    for value in forbidden:
        assert value not in logged
        assert value not in ledger
    [line] = [
        record for record in caplog.records if "WEBHOOK_DELIVERY_DEAD_LETTER" in record.getMessage()
    ]
    assert line.levelno == logging.WARNING
    assert line.getMessage() == (
        f"WEBHOOK_DELIVERY_DEAD_LETTER delivery_id={delivery_id} "
        f"endpoint_id={created['endpoint']['id']} attempts=3 error_code=http_status"
    )
