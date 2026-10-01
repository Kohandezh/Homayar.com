---
name: api-test
description: Generate pytest tests for the Homayar FastAPI backend (Homayar/backend). Covers /api/v1 endpoints, services, jobs, and the PostgreSQL-, Redis-, ffmpeg- and S3-marked suites. Uses the existing fixtures in backend/tests/conftest.py, never the network.
---

# Backend tests (Homayar/backend)

The backend is FastAPI; the suite is pytest under `Homayar/backend/tests/` (~70 files). Follow the patterns already there. Console tests (Vitest) and site tests (Playwright) are separate.

## Running

From `Homayar/`:

```bash
uv run pytest backend/tests -q -rs
uv run pytest backend/tests/test_members_api.py -q
uv run pytest backend/tests -q -k "share and expired"
```

Config lives in `Homayar/pyproject.toml` (`testpaths = ["backend/tests"]`, `pythonpath = ["."]`, `--strict-markers`). Imports are `backend.…`.

### Markers and what enables them

| Marker | Needs | Otherwise |
| --- | --- | --- |
| `postgres` | `TEST_DATABASE_URL=postgresql+psycopg://…` | skipped; default DB is a per-test SQLite file |
| `redis` | `TEST_REDIS_URL` | skipped |
| `ffmpeg` | `ffmpeg`/`ffprobe` on PATH | skipped (set `HOMAYAR_REQUIRE_MEDIA_TOOLS=1` to forbid skipping) |
| `live_s3` | `HOMAYAR_LIVE_S3_TEST=1` + `HOMAYAR_LIVE_S3_*` | skipped; mutates a disposable bucket |

CI sets `HOMAYAR_REQUIRE_POSTGRES_TESTS=1`, so a test that passes only on SQLite is not done. Anything about RLS, constraints, or concurrency gets a `postgres` test.

## Fixtures (backend/tests/conftest.py)

- `settings` — a real `Settings` with `environment="test"`, a temp DB, local storage under `tmp_path`, `tasks_eager=True`, `_env_file=None` so a developer's `.env` never leaks in. Override by building your own or `settings.model_copy(update=…)`.
- `client` — `fastapi.testclient.TestClient` over `create_app(settings)`. It monkeypatches email and Asanak SMS senders and resets the rate limiter.
- `email_outbox`, `sms_outbox` — every verification/reset email and OTP SMS the app tried to send.
- Helpers: `register_user(client, email_outbox, …)` and `login_user(client, credentials)` walk the real register → verify → login flow. Use them instead of hand-writing auth.
- `_database.py`, `_transport.py`, `_live_s3.py` hold the DB, Redis/Celery, and S3 helpers. Put a new shared fake there or in `conftest.py`, not in a test file.

Never reach a live provider, SMS gateway, or cloud LLM. Providers have mocks (`backend/providers/mocks.py`); HTTP clients are stubbed with `respx`; time with `freezegun`.

## Test types

1. **Endpoint test** (default for `backend/routers/v1/*`): drive `/api/v1/...` through `client`, assert status, the JSON fields the console parses, and the error envelope `{"error": {"code", "message", "request_id"}}`.

```python
def test_an_anonymous_caller_cannot_invite_a_member(client):
    response = client.post(
        "/api/v1/members/invitations",
        json={"email": "x@example.com", "role": "admin"},
    )
    assert response.status_code == 401
    assert "code" in response.json()["error"]
```

   For a signed-in caller, get a session with `login_user(client, register_user(client, email_outbox))` and read the auth header/cookie the way the neighbouring tests do. Check the real route, fields, and error codes in the router before copying.

2. **Service/unit test** for pure logic: `backend/locations/normalize.py`, `privacy/text_pii.py`, `jobs/states.py`. No app, no client.
3. **Job test**: processor, leases, outbox, replay. Use the existing job tests as templates (`test_job_*.py`, `test_dispatch_outbox.py`); assert state transitions and fencing, not sleeps.
4. **PostgreSQL guarantee** (`@pytest.mark.postgres`): RLS isolation between tenants, constraints, concurrent writes. See `test_postgres_guarantees.py`.

## What to cover

- **Authorization matrix**: anonymous 401, wrong role 403, other tenant's record 404 (not 403, do not leak existence), API key without the capability (ADR 0013). `test_authorization_matrix.py` is the reference.
- **Validation**: missing/invalid fields → 422 in the standard envelope.
- **Limits and idempotency**: rate limits, OTP resend, idempotency keys, quota/entitlements.
- **Privacy**: redacted vs original transcript routing (ADRs 0023–0028); no PII, token, or OTP code in logs (`caplog`, assert absence).
- **Egress**: on-prem mode must not call a cloud provider (`test_egress_denial.py`).
- **Truthful states**: a degraded or failed stage is reported as such.

## Style

- One behaviour per test; the name is the sentence (`test_a_revoked_share_link_answers_404`).
- Plain functions with bare `assert`. Ruff `E,F,I,B,UP,S`, line length 100; a test credential needs `# noqa: S105` with a reason.
- Never delete, skip, or `xfail` a failing test to go green.

## Checklist

- Uses `client`/`settings` and the existing helpers, no network.
- Refusal paths covered, not only success.
- PostgreSQL-only behaviour has a `postgres` test.
- New migration passes `uv run ../infra/ci/check-migrations.sh backend`.
- `uv run pytest backend/tests -q -rs` passes.
