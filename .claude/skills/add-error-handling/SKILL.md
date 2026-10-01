---
name: add-error-handling
description: Add error handling, validation, and recovery to Homayar code (FastAPI backend, product console, or public Next.js site) using the error shapes and helpers the repo already has. Use when hardening a code path against failures.
---

Make the current code robust against failure without inventing a second error system. Reuse what exists.

## Where the error conventions live

| Surface | Convention |
| --- | --- |
| Backend `Homayar/backend` | Every error body is `{"error": {code, message, request_id, details?}}`, built by `_error_payload` in `backend/main.py`. Raise the app's HTTP exceptions with a stable `code`; never return an ad-hoc dict. Request bodies are Pydantic models under `backend/schemas/`. |
| Jobs `backend/jobs` | Failures are classified (`failures.py`, ADR 0009): transient ones retry, permanent ones dead-letter, and replay is explicit. A new failure goes into a class; do not add a bare retry loop. |
| Providers `backend/providers` | Same-policy fallback and provider health (ADR 0030). Egress is policy-controlled (`egress.py`); an on-prem/air-gapped deployment must not fall back to a cloud provider. |
| Product console `Homayar/frontend` | `src/lib/api-errors.ts` normalises responses; `src/lib/error-messages.ts` and `upload-errors.ts` turn codes into Persian/English text. Add the code there, never render a raw server string. |
| Public site (repo root) | Next.js 16 route handlers under `src/app/api`, Zod for input. It must keep working with no env vars (static data fallback, graceful checkout/demo messages). |

## Steps

1. **Find failure points.** External calls (providers, S3, Asanak SMS, Stripe, Neo4j), DB writes, file/media handling, async jobs, user input.
2. **Validate at the boundary.** Pydantic on the backend, Zod on the site. Uploads: size and content-type are enforced on the server (`backend/media/validation.py`); a client check is only a hint.
3. **Choose the behaviour.** Transient: retry through the existing job/provider mechanism. Permanent: a clear error `code`. Non-critical: degrade and expose the degradation truthfully (ADR 0003, 0015); never report a stage as succeeded when it did not.
4. **Log without leaking.** No secrets, tokens, OTP codes, raw phone numbers, or transcript PII in logs (`backend/privacy/`). Include `request_id`.
5. **UI states.** Loading, empty, error with retry, 401, 403. Text in both `fa` and `en`, RTL-safe.
6. **Test the failure path**, not just the success path (see the `api-test` skill).

Do not add comments that restate the code. Do not swallow an exception with a bare `except:` / `catch {}`.
