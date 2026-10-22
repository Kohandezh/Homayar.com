# Homayar Python client

A small client for the Homayar ingestion API. It uploads a recording, waits for the
processing job, and reads the results. Its only dependency is `httpx`.

It follows the OpenAPI contract in
[`backend/contracts/ingestion-v1.openapi.json`](../../backend/contracts/ingestion-v1.openapi.json).

## Install

```bash
pip install ./Homayar/sdk/python
```

Python 3.10 or newer.

## Auth

Create an API key in the console. Give it the scopes you need:
`meeting:create` to upload and `meeting:read` to read. The key is shown once.

```python
from homayar_client import HomayarClient

client = HomayarClient("https://api.example.com", "hk_...")
```

`base_url` is the server origin. The client adds `/api/v1`. Every request sends
`Authorization: Bearer <key>`. The client never uses cookies.

Ids in the path are percent-encoded, so an id like `../api-keys` cannot reach another
route. The ids `""`, `.` and `..` raise `ValueError` before anything is sent.

`timeout` is seconds per request (60 by default). If you pass your own `http_client`,
the client keeps that client's timeout unless you also pass `timeout=`.

## Use

```python
from pathlib import Path

accepted = client.upload_recording(Path("call.wav").read_bytes(), filename="call.wav")
meeting_id = accepted["meeting"]["id"]

job = client.wait_for_job(meeting_id)          # polls GET /meetings/{id}/job
if job["state"] == "completed":
    print(client.get_summary(meeting_id)["overview"])
```

Methods: `upload_recording`, `get_job`, `get_meeting`, `get_summary`, `get_transcript`,
`get_ingestion`, `wait_for_job`. Each returns the decoded JSON body.

`wait_for_job` stops at a terminal state: `completed`, `failed`, `cancelled` or
`dead_letter`. A terminal job can still be a failure, so check `state` before you read
results. It raises `TimeoutError` after `timeout` seconds (default 900).

## Errors

An error answer raises `HomayarAPIError` with `status_code`, `code`, `message`,
`request_id`, `details` and `retry_after`. Branch on `code`, for example
`api_key_invalid` (401), `api_key_scope_denied` (403) or `ingestion_conflict` (409).
Quote `request_id` when you ask for support.

## Retries

`RetryPolicy(max_attempts=4, base_delay=0.5, max_delay=30.0, max_retry_after=300.0)`.
`max_attempts` counts the first try.

| Answer | Retried? |
| --- | --- |
| `502`, `503`, `504` | Yes |
| `429` with `Retry-After` (rate limit) | Yes |
| Network error (`httpx.TransportError`: connect error, timeout) | Yes |
| `429` without `Retry-After` (quota) | No. Waiting does not help. |
| Any other `4xx`, and `500` | No |

- With `Retry-After`, the client waits exactly that many seconds, even above
  `max_delay`.
- Without it, retry `n` waits a random time between half and all of
  `min(max_delay, base_delay * 2 ** (n - 1))`. The random part (jitter) keeps many
  clients from retrying at the same moment.
- When `Retry-After` is above `max_retry_after` (default 300 seconds), the client does
  not wait. It raises `HomayarAPIError` at once, with `retry_after` set, and you decide
  what to do. This matters for the per-key rate limit: its window is one hour, so its
  `Retry-After` can be close to 3600. Pass `max_retry_after=None` to wait for any
  `Retry-After`.
- A `Retry-After` that is not a finite number of seconds (for example `inf` or a date)
  is ignored. A `429` with it is then final, like a quota `429`.
- After the last attempt, an error answer raises `HomayarAPIError`. A network error is
  raised again as the original `httpx` exception.

## Idempotency

Every upload sends an `Idempotency-Key`. If you do not pass one, the client makes one
per call. All retries of that call reuse the same key and the same bytes. So a retry
after a lost answer gets the first job back (`idempotent_replay: true`) and never
creates a second job.

To be safe across a process restart too, make the key yourself, store it, and pass it
again: `upload_recording(..., idempotency_key="order-1234-call")`. The same key with a
different file raises `HomayarAPIError` with `409 ingestion_conflict`. Keys are 8 to 128
characters from `A-Z a-z 0-9 . _ : -`.

## Example

[`examples/upload_and_wait.py`](examples/upload_and_wait.py) uploads a file, waits, and
prints the job state and the summary.

```bash
export HOMAYAR_BASE_URL=https://api.example.com
export HOMAYAR_API_KEY=hk_...
python examples/upload_and_wait.py call.wav
```

The script prints the `Idempotency-Key` it used. If it crashes, rerun it with that key
in `HOMAYAR_IDEMPOTENCY_KEY`. The server then replays the first upload
(`replayed: True`) instead of making a second job.

```bash
export HOMAYAR_IDEMPOTENCY_KEY=sdk-...
python examples/upload_and_wait.py call.wav
```

## Versions

`homayar_client.CONTRACT_VERSION` is the contract version the client was written for.
The client's major version is the contract's major version, and both follow the
`/api/v1` prefix. A minor contract change only adds things, so this client keeps
working. A new major contract comes with a new prefix and a new client major version.

The tests in `backend/tests/test_python_client.py` run this client against the real app.
