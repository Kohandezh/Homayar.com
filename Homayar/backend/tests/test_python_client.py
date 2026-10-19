"""HOM-85 (WP16-T01): the minimal Python client against the real API.

Two layers. Unit tests drive ``homayar_client`` through ``httpx.MockTransport``
to pin the retry rules. Contract tests drive it against the real app in
process (``TestClient``) with real API keys: upload -> 202 job -> results,
refused keys, the per-key rate limit and idempotent replays.
"""

from __future__ import annotations

import importlib.util
import io
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from homayar_client import CONTRACT_VERSION, HomayarAPIError, HomayarClient, RetryPolicy
from homayar_client.errors import parse_retry_after
from sqlalchemy import func, select

from backend.contracts.openapi_contract import load_snapshot
from backend.database.saas_models import ApiKey, JobState, ProcessingJob
from backend.jobs.processor import process_job
from backend.storage import create_object_storage
from backend.storage.base import StorageError
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _allow_uploads, _mint, _owner
from backend.tests.test_job_processor import _providers
from backend.tests.test_uploads_api import _valid_metadata

SDK_ROOT = Path(__file__).resolve().parents[2] / "sdk" / "python"
BASE_URL = "http://testserver"
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class FakeSleep:
    """Records every wait instead of sleeping; ``then`` runs on each call."""

    def __init__(self, then=None):
        self.delays: list[float] = []
        self._then = then

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        if self._then is not None:
            self._then()


def _error_body(code: str) -> dict:
    return {"error": {"code": code, "message": f"{code} message", "request_id": "req-1"}}


def _scripted(*responses):
    """A mock transport that answers with ``responses`` in order and records requests.

    An item is an ``httpx.Response`` or an exception class to raise.
    """
    sent: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        item = queue.pop(0)
        if isinstance(item, type) and issubclass(item, Exception):
            raise item("scripted failure", request=request)
        return item

    return httpx.Client(transport=httpx.MockTransport(handler)), sent


def _unit_client(*responses, retry=None):
    http, sent = _scripted(*responses)
    sleep = FakeSleep()
    client = HomayarClient(
        "https://api.example.com/", "hk_unit.secret", http_client=http, retry=retry, sleep=sleep
    )
    return client, sent, sleep


_OK_UPLOAD = httpx.Response(202, json={"job": {"id": "job-1"}, "idempotent_replay": False})


# --- unit: requests, errors and retry rules -----------------------------------------


def test_requests_carry_the_bearer_key_and_the_v1_prefix():
    client, sent, _ = _unit_client(
        httpx.Response(200, json={"id": "job-1", "state": "queued"}),
        httpx.Response(200, json={"id": "m-1"}),
        httpx.Response(200, json={"overview": "x"}),
        httpx.Response(200, json={"segments": []}),
        httpx.Response(200, json={"id": "ing-1"}),
        _OK_UPLOAD,
    )
    assert client.get_job("m-1") == {"id": "job-1", "state": "queued"}
    client.get_meeting("m-1")
    client.get_summary("m-1")
    client.get_transcript("m-1")
    client.get_ingestion("ing-1")
    client.upload_recording(b"audio", filename="call.wav")
    assert [request.url.path for request in sent] == [
        "/api/v1/meetings/m-1/job",
        "/api/v1/meetings/m-1",
        "/api/v1/meetings/m-1/summary",
        "/api/v1/meetings/m-1/transcript",
        "/api/v1/ingestions/ing-1",
        "/api/v1/uploads",
    ]
    assert all(r.headers["Authorization"] == "Bearer hk_unit.secret" for r in sent)
    assert all(r.url.host == "api.example.com" for r in sent)


def test_an_error_response_becomes_a_typed_exception():
    response = httpx.Response(
        404,
        json={
            "error": {
                "code": "job_not_found",
                "message": "No job",
                "request_id": "req-9",
                "details": {"meeting_id": "m-1"},
            }
        },
    )
    client, sent, sleep = _unit_client(response)
    with pytest.raises(HomayarAPIError) as raised:
        client.get_job("m-1")
    error = raised.value
    assert (error.status_code, error.code, error.message) == (404, "job_not_found", "No job")
    assert error.request_id == "req-9"
    assert error.details == {"meeting_id": "m-1"}
    assert error.retry_after is None
    assert len(sent) == 1 and sleep.delays == []


def test_a_non_json_error_still_raises_with_the_status_code():
    client, _, _ = _unit_client(
        httpx.Response(502, text="Bad Gateway", headers={"X-Request-ID": "edge-1"}),
        retry=RetryPolicy(max_attempts=1),
    )
    with pytest.raises(HomayarAPIError) as raised:
        client.get_meeting("m-1")
    assert raised.value.status_code == 502
    assert raised.value.code is None
    assert raised.value.request_id == "edge-1"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 413, 422, 500])
def test_client_errors_and_500_are_never_retried(status):
    client, sent, sleep = _unit_client(httpx.Response(status, json=_error_body("refused")))
    with pytest.raises(HomayarAPIError) as raised:
        client.upload_recording(b"audio", filename="call.wav")
    assert raised.value.status_code == status
    assert len(sent) == 1 and sleep.delays == []


def test_a_429_without_retry_after_is_raised_at_once():
    client, sent, sleep = _unit_client(
        httpx.Response(429, json=_error_body("audio_quota_exceeded"))
    )
    with pytest.raises(HomayarAPIError) as raised:
        client.get_meeting("m-1")
    assert raised.value.status_code == 429 and raised.value.retry_after is None
    assert len(sent) == 1 and sleep.delays == []


@pytest.mark.parametrize("status", [502, 503, 504])
def test_gateway_and_unavailable_answers_are_retried_with_backoff(status):
    policy = RetryPolicy(max_attempts=3, base_delay=1.0, max_delay=8.0)
    client, sent, sleep = _unit_client(
        httpx.Response(status, json=_error_body("busy")),
        httpx.Response(status, json=_error_body("busy")),
        httpx.Response(200, json={"id": "m-1"}),
        retry=policy,
    )
    assert client.get_meeting("m-1") == {"id": "m-1"}
    assert len(sent) == 3
    assert len(sleep.delays) == 2
    # Retry n waits between half and all of min(max_delay, base_delay * 2 ** (n - 1)).
    assert 0.5 <= sleep.delays[0] <= 1.0
    assert 1.0 <= sleep.delays[1] <= 2.0


def test_backoff_is_capped_by_max_delay():
    policy = RetryPolicy(max_attempts=10, base_delay=1.0, max_delay=3.0)
    assert all(policy.backoff(n) <= 3.0 for n in range(1, 10) for _ in range(20))
    assert all(policy.backoff(n) >= 1.5 for n in range(3, 10) for _ in range(20))


def test_retry_after_sets_a_minimum_wait():
    client, sent, sleep = _unit_client(
        httpx.Response(429, json=_error_body("api_key_rate_limited"), headers={"Retry-After": "7"}),
        httpx.Response(503, json=_error_body("storage_unavailable"), headers={"Retry-After": "30"}),
        httpx.Response(200, json={"id": "m-1"}),
        retry=RetryPolicy(max_attempts=3, max_delay=1.0),
    )
    assert client.get_meeting("m-1") == {"id": "m-1"}
    assert sleep.delays[0] >= 7 and sleep.delays[1] >= 30
    assert len(sent) == 3


def test_retries_stop_after_max_attempts_with_the_last_response():
    client, sent, sleep = _unit_client(
        httpx.Response(503, json=_error_body("first")),
        httpx.Response(503, json=_error_body("second")),
        httpx.Response(504, json=_error_body("last")),
        retry=RetryPolicy(max_attempts=3),
    )
    with pytest.raises(HomayarAPIError) as raised:
        client.get_meeting("m-1")
    assert (raised.value.status_code, raised.value.code) == (504, "last")
    assert len(sent) == 3 and len(sleep.delays) == 2


def test_a_retry_after_above_max_retry_after_fails_fast():
    client, sent, sleep = _unit_client(
        httpx.Response(
            429, json=_error_body("api_key_rate_limited"), headers={"Retry-After": "3600"}
        ),
        retry=RetryPolicy(max_retry_after=60),
    )
    with pytest.raises(HomayarAPIError) as raised:
        client.get_meeting("m-1")
    assert raised.value.retry_after == 3600
    assert len(sent) == 1 and sleep.delays == []


def test_the_default_policy_fails_fast_above_five_minutes():
    assert RetryPolicy().max_retry_after == 300
    client, sent, sleep = _unit_client(
        httpx.Response(
            429, json=_error_body("api_key_rate_limited"), headers={"Retry-After": "301"}
        ),
    )
    with pytest.raises(HomayarAPIError) as raised:
        client.get_meeting("m-1")
    assert raised.value.retry_after == 301
    assert len(sent) == 1 and sleep.delays == []

    # allow-control: exactly the limit is still waited for
    client, sent, sleep = _unit_client(
        httpx.Response(
            429, json=_error_body("api_key_rate_limited"), headers={"Retry-After": "300"}
        ),
        httpx.Response(200, json={"id": "m-1"}),
    )
    assert client.get_meeting("m-1") == {"id": "m-1"}
    assert sleep.delays == [300]


def test_max_retry_after_none_waits_for_any_retry_after():
    client, sent, sleep = _unit_client(
        httpx.Response(
            429, json=_error_body("api_key_rate_limited"), headers={"Retry-After": "3600"}
        ),
        httpx.Response(200, json={"id": "m-1"}),
        retry=RetryPolicy(max_retry_after=None),
    )
    assert client.get_meeting("m-1") == {"id": "m-1"}
    assert sleep.delays == [3600]


@pytest.mark.parametrize("value", ["inf", "Infinity", "nan", "-1", "1e400", "soon", ""])
def test_a_retry_after_that_is_not_a_finite_number_is_ignored(value):
    assert parse_retry_after(value) is None
    client, sent, sleep = _unit_client(
        httpx.Response(
            429, json=_error_body("api_key_rate_limited"), headers={"Retry-After": value}
        )
    )
    with pytest.raises(HomayarAPIError) as raised:
        client.get_meeting("m-1")
    # Without a usable Retry-After a 429 is final, and nothing reaches sleep().
    assert raised.value.retry_after is None
    assert len(sent) == 1 and sleep.delays == []


def test_valid_retry_after_values_are_parsed():
    assert parse_retry_after("0") == 0
    assert parse_retry_after("30") == 30
    assert parse_retry_after(" 2.5 ") == 2.5
    assert parse_retry_after(None) is None


def test_path_ids_are_encoded_so_they_cannot_change_the_route():
    client, sent, _ = _unit_client(
        *[httpx.Response(200, json={}) for _ in range(3)],
    )
    client.get_meeting("../api-keys")
    client.get_job("a/b?c#d")
    client.get_ingestion("id with space")
    assert [request.url.raw_path for request in sent] == [
        b"/api/v1/meetings/..%2Fapi-keys",
        b"/api/v1/meetings/a%2Fb%3Fc%23d/job",
        b"/api/v1/ingestions/id%20with%20space",
    ]


@pytest.mark.parametrize("bad_id", ["", ".", ".."])
def test_ids_that_are_dot_segments_are_refused_before_sending(bad_id):
    client, sent, _ = _unit_client()
    with pytest.raises(ValueError):
        client.get_job(bad_id)
    assert sent == []


def test_the_timeout_is_set_only_when_the_caller_asks():
    def recorder():
        timeouts: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            timeouts.append(request.extensions["timeout"])
            return httpx.Response(200, json={})

        return timeouts, handler

    # The caller's own client keeps its own timeout ...
    timeouts, handler = recorder()
    own = httpx.Client(transport=httpx.MockTransport(handler), timeout=7.0)
    HomayarClient(BASE_URL, "hk_unit.secret", http_client=own).get_meeting("m-1")
    assert timeouts[-1]["read"] == 7.0
    # ... unless timeout= is passed to HomayarClient.
    HomayarClient(BASE_URL, "hk_unit.secret", http_client=own, timeout=3.0).get_meeting("m-1")
    assert timeouts[-1]["read"] == 3.0


def test_transport_errors_are_retried_and_the_last_one_is_re_raised():
    client, sent, sleep = _unit_client(
        httpx.ConnectError,
        httpx.ReadTimeout,
        retry=RetryPolicy(max_attempts=2),
    )
    with pytest.raises(httpx.ReadTimeout):
        client.get_meeting("m-1")
    assert len(sent) == 2 and len(sleep.delays) == 1


def test_every_retry_of_one_upload_sends_the_same_idempotency_key():
    client, sent, _ = _unit_client(
        httpx.ConnectError,
        httpx.Response(503, json=_error_body("storage_unavailable"), headers={"Retry-After": "1"}),
        _OK_UPLOAD,
        _OK_UPLOAD,
    )
    client.upload_recording(io.BytesIO(b"file-bytes-1"), filename="call.wav", title="Call")
    client.upload_recording(b"file-bytes-2", filename="call.wav")
    first_call = {request.headers["Idempotency-Key"] for request in sent[:3]}
    assert len(first_call) == 1
    key = first_call.pop()
    assert IDEMPOTENCY_KEY.match(key)
    # A new call gets a new key; every attempt of a call sends the whole file again.
    assert sent[3].headers["Idempotency-Key"] != key
    assert all(b"file-bytes-1" in request.read() for request in sent[:3])


def test_wait_for_job_polls_until_a_terminal_state():
    client, sent, sleep = _unit_client(
        httpx.Response(200, json={"id": "job-1", "state": "queued"}),
        httpx.Response(200, json={"id": "job-1", "state": "running"}),
        httpx.Response(200, json={"id": "job-1", "state": "dead_letter"}),
    )
    job = client.wait_for_job("m-1", timeout=60, poll_interval=2)
    assert job["state"] == "dead_letter"
    assert len(sent) == 3 and sleep.delays == [2, 2]


def test_wait_for_job_raises_on_timeout():
    running = httpx.Response(200, json={"id": "job-1", "state": "running"})
    client, sent, sleep = _unit_client(*[running] * 10)
    with pytest.raises(TimeoutError):
        client.wait_for_job("m-1", timeout=5, poll_interval=2)
    assert sum(sleep.delays) <= 5
    assert len(sent) <= 4


def test_the_client_package_imports_nothing_from_backend():
    sources = list((SDK_ROOT / "homayar_client").rglob("*.py"))
    sources.append(SDK_ROOT / "examples" / "upload_and_wait.py")
    assert len(sources) >= 2
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(from|import)\s+backend\b", text, re.MULTILINE), source


# --- contract: the client against the real app ---------------------------------------


class AppTransport(httpx.BaseTransport):
    """Sends each request into the in-process app and records it.

    ``fail_first`` can make the first attempt fail: ``"connect"`` raises before
    the app sees the request; ``"lost_response"`` lets the app handle it and
    then raises, as when a network drops the answer.
    """

    def __init__(self, test_client, *, fail_first: str | None = None):
        self.test_client = test_client
        self.sent: list[httpx.Request] = []
        self.answers: list[httpx.Response] = []
        self.fail_first = fail_first

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        failure, self.fail_first = self.fail_first, None
        if failure == "connect":
            raise httpx.ConnectError("scripted connect failure", request=request)
        answer = self.test_client.send(request)
        self.answers.append(answer)
        if failure == "lost_response":
            raise httpx.ReadTimeout("scripted lost response", request=request)
        return httpx.Response(answer.status_code, headers=answer.headers, content=answer.content)


def _api_client(test_client, raw_key, *, sleep=None, retry=None, fail_first=None):
    # No session cookie may ride along: the client authenticates with the key only.
    test_client.cookies.clear()
    transport = AppTransport(test_client, fail_first=fail_first)
    client = HomayarClient(
        BASE_URL,
        raw_key,
        http_client=httpx.Client(transport=transport),
        retry=retry,
        sleep=sleep or FakeSleep(),
    )
    return client, transport


def _workspace_keys(client, email_outbox, *scope_sets):
    _, workspace_id, cookies, headers = _owner(client, email_outbox)
    keys = []
    for index, scopes in enumerate(scope_sets):
        minted = _mint(client, headers, cookies, name=f"sdk-{index}", scopes=scopes)
        assert minted.status_code == 201, minted.text
        keys.append(minted.json())
    return workspace_id, cookies, headers, keys


def _job_count(workspace_id: str) -> int:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(func.count())
            .select_from(ProcessingJob)
            .where(ProcessingJob.workspace_id == workspace_id)
        )


def _assert_bearer_only(transport: AppTransport, raw_key: str) -> None:
    assert transport.sent
    for request in transport.sent:
        assert request.headers["Authorization"] == f"Bearer {raw_key}"
        assert "cookie" not in request.headers


def _fake_worker(settings, workspace_id: str):
    """Runs the real job processor on the workspace's queued jobs, as a worker would."""

    def work():
        with inspection_session_factory()() as db:
            queued = db.scalars(
                select(ProcessingJob.id).where(
                    ProcessingJob.workspace_id == workspace_id,
                    ProcessingJob.state == JobState.QUEUED,
                )
            ).all()
        for job_id in queued:
            process_job(
                job_id, worker_id="test-sdk-worker", settings=settings, providers=_providers()
            )

    return work


def test_upload_then_wait_for_the_job_then_read_results(
    client, email_outbox, settings, monkeypatch
):
    _allow_uploads(monkeypatch)
    workspace_id, _, _, [key] = _workspace_keys(
        client, email_outbox, ["meeting:read", "meeting:create"]
    )
    # Each wait between polls runs the real job processor, as a worker would.
    sleep = FakeSleep(then=_fake_worker(settings, workspace_id))
    sdk, transport = _api_client(client, key["key"], sleep=sleep)

    accepted = sdk.upload_recording(
        b"staged wav bytes", filename="call.wav", title="SDK call", content_type="audio/wav"
    )
    assert accepted["job"]["state"] == "queued"
    assert accepted["idempotent_replay"] is False
    meeting_id = accepted["meeting"]["id"]

    job = sdk.wait_for_job(meeting_id, timeout=30, poll_interval=1)
    assert job["state"] == "completed"
    assert sleep.delays == [1]
    assert job["id"] == accepted["job"]["id"]

    summary = sdk.get_summary(meeting_id)
    transcript = sdk.get_transcript(meeting_id)
    assert summary["meeting_id"] == meeting_id and summary["overview"]
    assert transcript["segments"]
    assert sdk.get_meeting(meeting_id)["id"] == meeting_id
    assert sdk.get_ingestion(accepted["ingestion"]["id"])["meeting_id"] == meeting_id
    _assert_bearer_only(transport, key["key"])


def _load_example():
    spec = importlib.util.spec_from_file_location(
        "upload_and_wait", SDK_ROOT / "examples" / "upload_and_wait.py"
    )
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    return example


def test_the_example_uploads_waits_and_prints_the_summary(
    client, email_outbox, settings, monkeypatch, tmp_path, capsys
):
    _allow_uploads(monkeypatch)
    workspace_id, _, _, [key] = _workspace_keys(
        client, email_outbox, ["meeting:read", "meeting:create"]
    )
    example = _load_example()
    audio = tmp_path / "call.wav"
    audio.write_bytes(b"staged wav bytes")

    sdk, transport = _api_client(
        client, key["key"], sleep=FakeSleep(then=_fake_worker(settings, workspace_id))
    )
    summary = example.run(sdk, audio)
    output = capsys.readouterr().out
    assert "completed" in output
    assert summary["overview"] in output
    assert f"Idempotency-Key: {transport.sent[0].headers['Idempotency-Key']}" in output
    _assert_bearer_only(transport, key["key"])


def test_the_example_reruns_safely_with_a_key_from_the_environment(
    client, email_outbox, settings, monkeypatch, tmp_path, capsys
):
    _allow_uploads(monkeypatch)
    workspace_id, _, _, [key] = _workspace_keys(
        client, email_outbox, ["meeting:read", "meeting:create"]
    )
    example = _load_example()
    audio = tmp_path / "call.wav"
    audio.write_bytes(b"staged wav bytes")
    sdk, transport = _api_client(
        client, key["key"], sleep=FakeSleep(then=_fake_worker(settings, workspace_id))
    )
    monkeypatch.setattr(example, "HomayarClient", lambda base_url, api_key: sdk)
    monkeypatch.setenv("HOMAYAR_BASE_URL", BASE_URL)
    monkeypatch.setenv("HOMAYAR_API_KEY", key["key"])
    monkeypatch.setenv("HOMAYAR_IDEMPOTENCY_KEY", "example-rerun-0001")

    assert example.main(["upload_and_wait.py", str(audio)]) == 0
    assert example.main(["upload_and_wait.py", str(audio)]) == 0  # a rerun
    output = capsys.readouterr().out
    assert output.count("Idempotency-Key: example-rerun-0001") == 2
    assert "replayed: True" in output
    uploads = [request for request in transport.sent if request.url.path == "/api/v1/uploads"]
    assert [request.headers["Idempotency-Key"] for request in uploads] == [
        "example-rerun-0001",
        "example-rerun-0001",
    ]
    assert _job_count(workspace_id) == 1


def test_refused_keys_raise_and_are_not_retried(client, email_outbox, settings, monkeypatch):
    _allow_uploads(monkeypatch)
    _, cookies, headers, [revoked, expired, read_only, live] = _workspace_keys(
        client,
        email_outbox,
        ["meeting:read", "meeting:create"],
        ["meeting:read", "meeting:create"],
        ["meeting:read"],
        ["meeting:read", "meeting:create"],
    )
    gone = client.delete(f"/api/v1/api-keys/{revoked['id']}", headers=headers, cookies=cookies)
    assert gone.status_code == 204
    with inspection_session_factory()() as db:
        db.get(ApiKey, expired["id"]).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    for refused, status, code in (
        (revoked, 401, "api_key_invalid"),
        (expired, 401, "api_key_invalid"),
        (read_only, 403, "api_key_scope_denied"),
    ):
        sleep = FakeSleep()
        sdk, transport = _api_client(client, refused["key"], sleep=sleep)
        with pytest.raises(HomayarAPIError) as raised:
            sdk.upload_recording(b"staged wav bytes", filename="call.wav")
        assert (raised.value.status_code, raised.value.code) == (status, code), refused["name"]
        assert raised.value.request_id
        assert len(transport.sent) == 1 and sleep.delays == []

    # allow-controls: a live write key uploads; the read-only key reads that meeting
    writer, _ = _api_client(client, live["key"])
    accepted = writer.upload_recording(b"staged wav bytes", filename="call.wav")
    assert accepted["job"]["id"]
    reader, transport = _api_client(client, read_only["key"])
    meeting_id = accepted["meeting"]["id"]
    assert reader.get_job(meeting_id)["id"] == accepted["job"]["id"]
    assert reader.get_meeting(meeting_id)["id"] == meeting_id
    _assert_bearer_only(transport, read_only["key"])


def test_the_per_key_rate_limit_is_honoured_then_raised(
    client, email_outbox, settings, monkeypatch
):
    _, _, _, [key] = _workspace_keys(client, email_outbox, ["meeting:read"])
    monkeypatch.setattr(settings, "api_key_rate_limit_per_hour", 1)
    sleep = FakeSleep()
    # The window is one hour: opt out of the default fail-fast limit (300 s).
    policy = RetryPolicy(max_attempts=3, max_retry_after=None)
    sdk, transport = _api_client(client, key["key"], sleep=sleep, retry=policy)

    with pytest.raises(HomayarAPIError) as first:
        sdk.get_meeting("not-a-meeting")
    assert first.value.status_code == 404  # the first request is inside the limit

    with pytest.raises(HomayarAPIError) as raised:
        sdk.get_meeting("not-a-meeting")
    error = raised.value
    assert (error.status_code, error.code) == (429, "api_key_rate_limited")
    assert error.retry_after is not None and error.retry_after >= 1
    assert len(transport.sent) == 1 + 3
    # Each wait is at least the Retry-After of the 429 just before it.
    limited = transport.answers[1:]
    assert [answer.status_code for answer in limited] == [429, 429, 429]
    assert len(sleep.delays) == 2
    for delay, answer in zip(sleep.delays, limited, strict=False):
        assert delay >= float(answer.headers["Retry-After"])
    _assert_bearer_only(transport, key["key"])


def test_the_default_policy_raises_the_hour_long_rate_limit_at_once(
    client, email_outbox, settings, monkeypatch
):
    _, _, _, [key] = _workspace_keys(client, email_outbox, ["meeting:read"])
    monkeypatch.setattr(settings, "api_key_rate_limit_per_hour", 1)
    sleep = FakeSleep()
    sdk, transport = _api_client(client, key["key"], sleep=sleep)
    with pytest.raises(HomayarAPIError):
        sdk.get_meeting("not-a-meeting")  # 404, inside the limit
    with pytest.raises(HomayarAPIError) as raised:
        sdk.get_meeting("not-a-meeting")
    assert (raised.value.status_code, raised.value.code) == (429, "api_key_rate_limited")
    assert raised.value.retry_after > 300
    assert len(transport.sent) == 2 and sleep.delays == []


def test_an_id_with_a_slash_does_not_reach_another_route(client, email_outbox, settings):
    _, _, _, [key] = _workspace_keys(client, email_outbox, ["meeting:read"])
    sdk, transport = _api_client(client, key["key"])
    with pytest.raises(HomayarAPIError) as raised:
        sdk.get_meeting("../api-keys")
    # Unencoded, this would be GET /api/v1/api-keys (403 api_key_scope_denied for a key).
    assert raised.value.status_code == 404
    assert transport.sent[0].url.raw_path == b"/api/v1/meetings/..%2Fapi-keys"


def test_a_quota_429_without_retry_after_is_raised_at_once(
    client, email_outbox, settings, monkeypatch
):
    _allow_uploads(monkeypatch)
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=settings.demo_quota_seconds + 60),
    )
    _, _, _, [key] = _workspace_keys(client, email_outbox, ["meeting:read", "meeting:create"])
    sleep = FakeSleep()
    sdk, transport = _api_client(client, key["key"], sleep=sleep)
    with pytest.raises(HomayarAPIError) as raised:
        sdk.upload_recording(b"long recording", filename="long.wav")
    assert (raised.value.status_code, raised.value.code) == (429, "audio_quota_exceeded")
    assert raised.value.retry_after is None
    assert len(transport.sent) == 1 and sleep.delays == []


def test_the_same_key_and_bytes_replay_and_different_bytes_conflict(
    client, email_outbox, settings, monkeypatch
):
    _allow_uploads(monkeypatch)
    workspace_id, _, _, [key] = _workspace_keys(
        client, email_outbox, ["meeting:read", "meeting:create"]
    )
    sleep = FakeSleep()
    sdk, transport = _api_client(client, key["key"], sleep=sleep)

    first = sdk.upload_recording(b"same bytes", filename="call.wav", idempotency_key="sdk-same-1")
    replay = sdk.upload_recording(b"same bytes", filename="call.wav", idempotency_key="sdk-same-1")
    assert first["idempotent_replay"] is False and replay["idempotent_replay"] is True
    assert replay["job"]["id"] == first["job"]["id"]

    sent_before = len(transport.sent)
    with pytest.raises(HomayarAPIError) as raised:
        sdk.upload_recording(b"other bytes", filename="call.wav", idempotency_key="sdk-same-1")
    assert (raised.value.status_code, raised.value.code) == (409, "ingestion_conflict")
    assert len(transport.sent) == sent_before + 1 and sleep.delays == []
    assert _job_count(workspace_id) == 1
    _assert_bearer_only(transport, key["key"])


@pytest.mark.parametrize("failure", ["connect", "lost_response"])
def test_a_retried_upload_after_a_transport_error_creates_one_job(
    client, email_outbox, settings, monkeypatch, failure
):
    _allow_uploads(monkeypatch)
    workspace_id, _, _, [key] = _workspace_keys(
        client, email_outbox, ["meeting:read", "meeting:create"]
    )
    sleep = FakeSleep()
    sdk, transport = _api_client(client, key["key"], sleep=sleep, fail_first=failure)

    accepted = sdk.upload_recording(b"retried bytes", filename="call.wav")
    assert len(transport.sent) == 2 and len(sleep.delays) == 1
    keys = {request.headers["Idempotency-Key"] for request in transport.sent}
    assert len(keys) == 1
    # When the first answer was lost, the server already had the job: the retry replays it.
    assert accepted["idempotent_replay"] is (failure == "lost_response")
    assert _job_count(workspace_id) == 1
    _assert_bearer_only(transport, key["key"])


def test_a_retried_upload_after_a_real_503_creates_one_job(
    client, email_outbox, settings, monkeypatch
):
    _allow_uploads(monkeypatch)
    real_storage = create_object_storage(settings)
    failures = {"left": 1}

    class FlakyStorage:
        def __getattr__(self, name):
            return getattr(real_storage, name)

        def put_stream(self, *args, **kwargs):
            if failures["left"]:
                failures["left"] -= 1
                raise StorageError("object store is down", code="storage_unavailable")
            return real_storage.put_stream(*args, **kwargs)

    monkeypatch.setattr(
        "backend.routers.v1.meetings.create_object_storage", lambda _settings: FlakyStorage()
    )
    workspace_id, _, _, [key] = _workspace_keys(
        client, email_outbox, ["meeting:read", "meeting:create"]
    )
    sleep = FakeSleep()
    sdk, transport = _api_client(client, key["key"], sleep=sleep)

    accepted = sdk.upload_recording(b"retried bytes", filename="call.wav")
    assert accepted["idempotent_replay"] is False
    assert len(transport.sent) == 2
    assert sleep.delays and sleep.delays[0] >= 30  # the server's Retry-After: 30
    keys = {request.headers["Idempotency-Key"] for request in transport.sent}
    assert len(keys) == 1
    assert _job_count(workspace_id) == 1


def test_the_client_targets_the_published_contract_major_version():
    published = load_snapshot()["info"]["version"]
    assert CONTRACT_VERSION.split(".")[0] == published.split(".")[0]
