"""A small client for the Homayar ingestion API, contract v1."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any, BinaryIO
from urllib.parse import quote

import httpx

from homayar_client.errors import HomayarAPIError
from homayar_client.retry import RetryPolicy

API_PREFIX = "/api/v1"

# Job states after which the job never changes again. "queued" and "running" are not.
TERMINAL_JOB_STATES = frozenset({"completed", "failed", "cancelled", "dead_letter"})


class HomayarClient:
    """Uploads recordings and reads their results with an API key.

    ``base_url`` is the server origin, for example ``https://api.example.com``;
    the client adds ``/api/v1``. Pass ``http_client`` to send through your own
    ``httpx.Client`` (the client still sets ``Authorization`` on each request).
    ``sleep`` is how the client waits between retries and polls; tests pass a
    fake that only records the delays. ``timeout`` (seconds per request) is 60
    by default; with your own ``http_client`` the client keeps that client's
    timeout unless you pass ``timeout`` here too.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        http_client: httpx.Client | None = None,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=60.0 if timeout is None else timeout)
        self._retry = retry or RetryPolicy()
        self._sleep = sleep
        self._timeout = timeout

    def close(self) -> None:
        """Close the HTTP connection pool, if this client created it."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> HomayarClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- ingestion -------------------------------------------------------------------

    def upload_recording(
        self,
        file: bytes | BinaryIO,
        *,
        filename: str,
        title: str | None = None,
        language: str = "fa",
        content_type: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Upload one recording. Returns the ``202`` body with ``meeting`` and ``job``.

        Without ``idempotency_key`` the client makes a new one for this call.
        Every retry of the call sends the same key and the same bytes, so the
        server creates at most one job. Pass your own key to stay safe across
        process restarts too. The file is read into memory once.
        """
        content = file if isinstance(file, bytes) else file.read()
        form = {"language": language}
        if title is not None:
            form["title"] = title
        response = self._request(
            "POST",
            "/uploads",
            headers={"Idempotency-Key": idempotency_key or new_idempotency_key()},
            data=form,
            files={"file": (filename, content, content_type or "application/octet-stream")},
        )
        return response.json()

    # --- reads -----------------------------------------------------------------------

    def get_job(self, meeting_id: str) -> dict[str, Any]:
        return self._get(f"/meetings/{_segment(meeting_id)}/job")

    def get_meeting(self, meeting_id: str) -> dict[str, Any]:
        return self._get(f"/meetings/{_segment(meeting_id)}")

    def get_summary(self, meeting_id: str) -> dict[str, Any]:
        return self._get(f"/meetings/{_segment(meeting_id)}/summary")

    def get_transcript(self, meeting_id: str) -> dict[str, Any]:
        return self._get(f"/meetings/{_segment(meeting_id)}/transcript")

    def get_ingestion(self, ingestion_id: str) -> dict[str, Any]:
        return self._get(f"/ingestions/{_segment(ingestion_id)}")

    def wait_for_job(
        self, meeting_id: str, *, timeout: float = 900.0, poll_interval: float = 5.0
    ) -> dict[str, Any]:
        """Poll the meeting's job until its state is terminal, then return the job.

        A terminal job may have failed: check ``job["state"] == "completed"``
        before you read results. Raises ``TimeoutError`` after ``timeout``
        seconds. Time counts both the real clock and the waits between polls,
        so a fake ``sleep`` in tests still reaches the timeout.
        """
        started = time.monotonic()
        waited = 0.0
        while True:
            job = self.get_job(meeting_id)
            if job["state"] in TERMINAL_JOB_STATES:
                return job
            elapsed = max(time.monotonic() - started, waited)
            if elapsed + poll_interval > timeout:
                raise TimeoutError(
                    f"Job {job['id']} is still {job['state']} after {elapsed:.0f} seconds"
                )
            self._sleep(poll_interval)
            waited += poll_interval

    # --- transport -------------------------------------------------------------------

    def _get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path).json()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send one call, retrying it as ``RetryPolicy`` allows.

        After the last attempt an error answer raises ``HomayarAPIError`` and a
        network error is raised again as the original ``httpx.TransportError``.
        """
        url = self._base_url + API_PREFIX + path
        headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {self._api_key}"}
        if self._timeout is not None:
            kwargs["timeout"] = self._timeout
        attempt = 1
        while True:
            try:
                response = self._http.request(method, url, headers=headers, **kwargs)
            except httpx.TransportError:
                if attempt >= self._retry.max_attempts:
                    raise
                delay = self._retry.backoff(attempt)
            else:
                if response.is_success:
                    return response
                error = HomayarAPIError.from_response(response)
                retryable = self._retry.should_retry(error.status_code, error.retry_after)
                if not retryable or attempt >= self._retry.max_attempts:
                    raise error
                delay = error.retry_after
                if delay is None:
                    delay = self._retry.backoff(attempt)
            self._sleep(delay)
            attempt += 1


def _segment(value: str) -> str:
    """One URL path segment: ``/``, ``?``, ``#`` and the like are percent-encoded.

    So an id can never point the request at another route, for example
    ``get_meeting("../api-keys")``. The dot segments ``.`` and ``..`` are
    refused, because HTTP libraries and proxies resolve them.
    """
    if value in ("", ".", ".."):
        raise ValueError(f"Not a valid id: {value!r}")
    return quote(value, safe="")


def new_idempotency_key() -> str:
    """A random key in the server's format (8 to 128 of ``A-Z a-z 0-9 . _ : -``)."""
    return f"sdk-{uuid.uuid4()}"
