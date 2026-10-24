"""The versioned OpenAPI contract of the asynchronous ingestion API (HOM-85, WP16-T01).

``build_contract`` cuts the public ingestion operations out of the live
``app.openapi()`` schema and adds what FastAPI cannot infer: the error
responses (all in the ``{"error": {...}}`` envelope of ``backend.main``), the
``Retry-After`` header and the descriptions of the API flow. The result is
checked in as ``ingestion-v1.openapi.json``; the test suite and ``--check``
fail when the live app drifts from it.

The error table lives here, not in the routers, so documenting the contract
never touches a route (and the live ``/api/openapi.json`` stays as it is). The
table only fills gaps: when a route documents a status the table also names,
the build stops with ``LookupError`` instead of hiding the route's entry.

Run from ``Homayar/``::

    uv run python -m backend.contracts.openapi_contract --check
    uv run python -m backend.contracts.openapi_contract --write
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI

SNAPSHOT_PATH = Path(__file__).with_name("ingestion-v1.openapi.json")
WRITE_COMMAND = "uv run python -m backend.contracts.openapi_contract --write"

# The major number follows the /api/v1 prefix. Additive change: bump the minor.
CONTRACT_VERSION = "1.5.0"

SECURITY_SCHEME = "HTTPBearer"

DESCRIPTION = """\
Asynchronous ingestion API of Homayar, contract version 1.

**Auth.** Send an API key on every request: `Authorization: Bearer <api key>`.
A workspace admin creates keys in the console. A key acts in one workspace with
the scopes it was given: `meeting:create` to upload, `meeting:read` to read. A
revoked or expired key gets `401 api_key_invalid`. A missing scope gets
`403 api_key_scope_denied`. The cookie and CSRF parameters below belong to the
browser console; API clients do not send them.

**Flow.** 1) `POST /api/v1/uploads` (or `/api/v1/voicemails`) answers `202`
with the meeting and its processing job. 2) Poll
`GET /api/v1/meetings/{meeting_id}/job` until `state` is terminal: `completed`,
`failed`, `cancelled` or `dead_letter` (`queued` and `running` are not). 3) After
`completed`, read the results: `/transcript`, `/summary`, `/actions`,
`/evidence`, `/quality`, `/export`.

**Idempotency.** Every upload sends an `Idempotency-Key` header
(8 to 128 characters from `A-Z a-z 0-9 . _ : -`). The same key with the same
file and fields is a replay: `202` with the original job and
`idempotent_replay: true`, and no new job. The same key with a different
payload is `409 ingestion_conflict`. A malformed key is
`400 invalid_idempotency_key`. Keep the key when you retry, so a retry can never
create a second job.

**Role-scoped trend views (1.3.0).** Four read operations need a signed-in user
session and refuse every API key with `401 api_key_not_accepted`: the team comparison
`/api/v1/trends/teams`, the agent self-view `/api/v1/trends/me`, the executive aggregates
and the analyst drill-down under `/api/v1/organizations/{organization_id}`. Each cohort
carries a `disclosure` block; a cohort of 1 to 4 agents is held back.

**Supporting conversations (1.4.0).** `/api/v1/trends/conversations` lists the
conversations behind one `computed` cell of `/api/v1/trends/issues`, `/api/v1/trends/teams`
or `/api/v1/trends/me`, as ids only. It needs a signed-in user session too. Every trend
answer carries `notices: ["correlation_not_cause"]`: a trend shows how often an issue was
counted, not why.

**Aggregate metadata (1.5.0).** `/api/v1/voicemails/analytics` adds `coverage` (the
`coverage/1` processing and evaluation counters of the voicemails it counts), `scorecard`
and `missing_data`. Each item of `/api/v1/organizations/{organization_id}/aggregates` adds
the `coverage`, `scorecard`, `missing_data` and `disclosure` of the matching executive
trend block: a block of 1 to 4 agents in some closed week is withheld, with `coverage` and
`missing_data` null. Every existing field keeps its name and value.

**Errors.** Every error body is `{"error": {"code", "message", "request_id",
"details"?}}`. `code` is stable; branch on it, not on `message`. Every response
carries `X-Request-ID`.

**Retries.** When a `429` or `503` carries `Retry-After` (seconds), wait at
least that long before the next attempt. A `429` without `Retry-After` is a
quota (`audio_quota_exceeded`, `concurrent_job_limit_reached`): waiting does
not help, so do not retry it. `502`, `503`, `504` and network errors are safe
to retry with backoff. Other `4xx` and `500` are not.
"""

# Extra description per operation, appended to the route's own docstring.
OPERATION_NOTES: dict[tuple[str, str], str] = {
    ("post", "/api/v1/uploads"): (
        "Accepts one recording for processing and answers `202` with the meeting, "
        "the recording and the queued job. Needs `meeting:create` and a verified owner. "
        "The `Idempotency-Key` header is required: the same key and payload replay the "
        "first answer (`idempotent_replay: true`), the same key with another payload is "
        "`409 ingestion_conflict`. Rate-limit `429` and transient `503` carry `Retry-After`."
    ),
    ("post", "/api/v1/voicemails"): (
        "Imports one voicemail and answers `202` with its job. `external_message_id` is "
        "the idempotency key: the same id and payload replay, another payload is `409`."
    ),
    ("get", "/api/v1/meetings/{meeting_id}/job"): (
        "The meeting's latest processing job. Poll it after an upload until `state` is "
        "`completed`, `failed`, `cancelled` or `dead_letter`."
    ),
    ("get", "/api/v1/trends/teams"): (
        "Since 1.3.0. Who: a workspace owner or admin (every active team) or a member or "
        "viewer with an effective supervisor assignment (only those teams); `meeting:read`. "
        "A signed-in session only: an API key is `401 api_key_not_accepted`. `403 "
        "team_scope_denied` for a team the caller may not compare, `422 "
        "trend_too_many_teams` over 10 teams, `422 trend_parameter_not_allowed` for "
        "`agent_user_id`. No rank: teams in a fixed order, differences left minus right."
    ),
    ("get", "/api/v1/trends/me"): (
        "Since 1.3.0. Who: any workspace member with `meeting:read`; the caller's own "
        "meetings as agent. A signed-in session only: an API key is "
        "`401 api_key_not_accepted`. `422 trend_parameter_not_allowed` for `agent_user_id`."
    ),
    ("get", "/api/v1/organizations/{organization_id}/trends/issues"): (
        "Since 1.3.0. Who: an organization owner, admin, analyst or executive "
        "(`organization:analytics:read`), over the workspaces they may see. A signed-in "
        "session only: an API key is `401 api_key_not_accepted`; no membership is `404 "
        "organization_not_found`. Whole closed weeks from Saturday 00:00 Asia/Tehran "
        "(`422 trend_window_not_aligned` otherwise), grain `none` or `week`."
    ),
    ("get", "/api/v1/organizations/{organization_id}/workspaces/{workspace_id}/trends/issues"): (
        "Since 1.3.0. Who: an organization owner, admin or analyst "
        "(`organization:records:read`); an executive is `403 permission_denied`. A signed-in "
        "session only: an API key is `401 api_key_not_accepted`. A workspace outside the "
        "caller's scope is `404 workspace_not_found`. The query of the executive aggregates."
    ),
    ("get", "/api/v1/trends/conversations"): (
        "Since 1.4.0. Who: any workspace member with `meeting:read`, inside their record "
        "scope. A signed-in session only: an API key is `401 api_key_not_accepted`. "
        "`trend_view` is `issues`, `teams` (one `team_id` the caller may compare, else `403 "
        "team_scope_denied`) or `me`; the rest is that view's query, with `from` and `to` "
        "required (`422 trend_window_required`). `row` names the cell, `point` a point, "
        "`offset` and `limit` (at most 100) the page. Only a `computed` cell lists the "
        "conversations of its numerator, by meeting id; every other cell is `403 "
        "trend_links_withheld`. A link holds ids only, never text."
    ),
    ("get", "/api/v1/voicemails/analytics"): (
        "Since 1.5.0: `coverage` (basis `voicemail_population`, window basis "
        "`voicemail_received_at`), `scorecard` and `missing_data` over the voicemails that "
        "`total` counts, so `coverage.processing.denominator` equals `total`. A window "
        "longer than 366 days or not after its start is `422 invalid_window`."
    ),
    ("get", "/api/v1/organizations/{organization_id}/aggregates"): (
        "Since 1.5.0. Who: an organization owner, admin, analyst or executive "
        "(`organization:analytics:read`), over the workspaces they may see. A signed-in "
        "session only: an API key is `401 api_key_not_accepted`. Each item adds "
        "`coverage`, `scorecard`, `missing_data` and `disclosure`: exactly those of the "
        "workspace block of `GET /api/v1/organizations/{organization_id}/trends/issues` for "
        "the same caller, grant and window. `coverage_window` is the window that "
        "`GET /api/v1/organizations/{organization_id}/trends/issues` would use for "
        "`from=since` and `to=until`, or its default (the 4 closed weeks before this week) "
        "when that route would refuse them. A withheld item has `coverage` and "
        "`missing_data` null; its other fields keep their values and their own window."
    ),
    ("get", "/api/v1/meetings/{meeting_id}/transcript"): (
        "Since 1.1.0: `speaker_key` (repeatable), `start_ms` and `end_ms` filter the "
        "segments. A window whose `end_ms` is not after `start_ms` is "
        "`422 invalid_time_range`. `filters` echoes the filter, `total_segments` counts the "
        "revision before filtering, and `completeness` names what is missing from the "
        "meeting's results."
    ),
}

ERROR_DESCRIPTIONS: dict[int, str] = {
    400: "Bad request, for example `invalid_idempotency_key`.",
    401: "Missing, malformed, revoked or expired API key (`api_key_invalid`).",
    403: "Scope or permission refused (`api_key_scope_denied`, `permission_denied`, "
    "`verified_identity_required`).",
    404: "Not found in this workspace.",
    409: "Same idempotency key or event id with a different payload (`ingestion_conflict`).",
    410: "The event was ingested before, but its conversation was deleted.",
    413: "The recording is larger or longer than allowed.",
    422: "Request or media validation failed (`validation_error` and media codes).",
    429: "Rate limited (with `Retry-After`) or quota exhausted (without `Retry-After`).",
    503: "Temporarily unavailable. Retry with backoff; honour `Retry-After` when present.",
}
_UPLOAD_ERRORS = (400, 401, 403, 409, 410, 413, 422, 429, 503)
_READ_ERRORS = (401, 403, 404, 422, 429, 503)

# The in-scope operations and the error statuses each one documents.
OPERATIONS: dict[tuple[str, str], tuple[int, ...]] = {
    ("post", "/api/v1/uploads"): _UPLOAD_ERRORS,
    ("post", "/api/v1/voicemails"): tuple(s for s in _UPLOAD_ERRORS if s != 400),
    ("get", "/api/v1/meetings/{meeting_id}"): _READ_ERRORS,
    ("get", "/api/v1/meetings/{meeting_id}/job"): _READ_ERRORS,
    ("get", "/api/v1/ingestions"): (401, 403, 422, 429, 503),
    ("get", "/api/v1/ingestions/{ingestion_id}"): _READ_ERRORS,
    ("get", "/api/v1/meetings/{meeting_id}/transcript"): _READ_ERRORS,
    ("get", "/api/v1/meetings/{meeting_id}/summary"): _READ_ERRORS,
    ("get", "/api/v1/meetings/{meeting_id}/actions"): _READ_ERRORS,
    ("get", "/api/v1/meetings/{meeting_id}/evidence"): _READ_ERRORS,
    ("get", "/api/v1/meetings/{meeting_id}/quality"): _READ_ERRORS,
    ("get", "/api/v1/meetings/{meeting_id}/export"): _READ_ERRORS,
    ("get", "/api/v1/trends/teams"): (401, 403, 422, 429, 503),
    ("get", "/api/v1/trends/me"): (401, 403, 422, 429, 503),
    ("get", "/api/v1/trends/conversations"): (401, 403, 422, 429, 503),
    ("get", "/api/v1/organizations/{organization_id}/trends/issues"): (401, 404, 422, 429, 503),
    (
        "get",
        "/api/v1/organizations/{organization_id}/workspaces/{workspace_id}/trends/issues",
    ): _READ_ERRORS,
    ("get", "/api/v1/voicemails/analytics"): (401, 403, 422, 429, 503),
    ("get", "/api/v1/organizations/{organization_id}/aggregates"): (401, 404, 422, 429, 503),
}

# The route returns a plain ``Response``, so FastAPI documents it as empty JSON.
EXPORT_PATH = "/api/v1/meetings/{meeting_id}/export"
EXPORT_CONTENT = {
    "application/json": {"schema": {"type": "object"}},
    "application/x-subrip": {"schema": {"type": "string"}},
    "text/plain": {"schema": {"type": "string"}},
    "text/vtt": {"schema": {"type": "string"}},
}

# The body ``backend.main._error_payload`` sends for every error.
ERROR_SCHEMAS: dict[str, dict[str, Any]] = {
    "ErrorResponse": {
        "title": "ErrorResponse",
        "type": "object",
        "required": ["error"],
        "properties": {"error": {"$ref": "#/components/schemas/ErrorDetail"}},
    },
    "ErrorDetail": {
        "title": "ErrorDetail",
        "type": "object",
        "required": ["code", "message", "request_id"],
        "properties": {
            "code": {"type": "string", "description": "Stable machine-readable code."},
            "message": {"type": "string", "description": "Human-readable text."},
            "request_id": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Same value as the X-Request-ID response header.",
            },
            "details": {
                "anyOf": [{"type": "object"}, {"type": "array", "items": {}}],
                "description": "Optional structured context, for example validation fields.",
            },
        },
    },
}

RETRY_AFTER_HEADER = {
    "description": "Seconds to wait before retrying. Sent with rate limits and transient "
    "failures; absent on quota 429s.",
    "schema": {"type": "integer", "minimum": 1},
}

_SCHEMA_REF = "#/components/schemas/"

# What FastAPI writes by itself. The builder replaces only these exact entries.
_FASTAPI_422 = {
    "description": "Validation Error",
    "content": {"application/json": {"schema": {"$ref": _SCHEMA_REF + "HTTPValidationError"}}},
}
_FASTAPI_EMPTY_200 = {
    "description": "Successful Response",
    "content": {"application/json": {"schema": {}}},
}


def build_contract(app: FastAPI) -> dict[str, Any]:
    """Build the contract document from the live schema of ``app``."""
    live = copy.deepcopy(app.openapi())
    paths: dict[str, dict[str, Any]] = {}
    for (method, path), error_statuses in OPERATIONS.items():
        try:
            operation = live["paths"][path][method]
        except KeyError:
            raise LookupError(f"The live app has no {method.upper()} {path}") from None
        _document_errors(operation, error_statuses, name=f"{method.upper()} {path}")
        note = OPERATION_NOTES.get((method, path))
        if note:
            own = operation.get("description")
            operation["description"] = f"{own}\n\n{note}" if own else note
        paths.setdefault(path, {})[method] = operation
    export = paths[EXPORT_PATH]["get"]["responses"]["200"]
    if export != _FASTAPI_EMPTY_200:
        raise LookupError(
            f"GET {EXPORT_PATH} now documents its 200 response on the route; "
            "remove EXPORT_CONTENT so the live answer reaches the contract"
        )
    export["content"] = copy.deepcopy(EXPORT_CONTENT)

    clash = ERROR_SCHEMAS.keys() & live["components"]["schemas"].keys()
    if clash:
        raise LookupError(f"The live app already defines {sorted(clash)}; rename one")
    available = {**live["components"]["schemas"], **copy.deepcopy(ERROR_SCHEMAS)}
    return {
        "openapi": live["openapi"],
        "info": {
            "title": "Homayar Ingestion API",
            "version": CONTRACT_VERSION,
            "description": DESCRIPTION,
        },
        "security": [{SECURITY_SCHEME: []}],
        "paths": paths,
        "components": {
            "schemas": _referenced_schemas(paths, available),
            "securitySchemes": {
                SECURITY_SCHEME: live["components"]["securitySchemes"][SECURITY_SCHEME]
            },
        },
    }


def _document_errors(operation: dict[str, Any], statuses: tuple[int, ...], *, name: str) -> None:
    """Add the table's error responses, without ever hiding one the route documents.

    Only FastAPI's own 422 (``HTTPValidationError``, which the app never sends)
    is replaced. Any other live entry for a status the table also names stops
    the build: remove that status from ``OPERATIONS`` and the live entry is used.
    """
    for status in statuses:
        live = operation["responses"].get(str(status))
        if live is not None and not (status == 422 and live == _FASTAPI_422):
            raise LookupError(
                f"{name} documents {status} on the route itself; remove {status} from "
                f"its row in OPERATIONS ({__name__}) so the live entry is used"
            )
        response: dict[str, Any] = {
            "description": ERROR_DESCRIPTIONS[status],
            "content": {"application/json": {"schema": {"$ref": _SCHEMA_REF + "ErrorResponse"}}},
        }
        if status in (429, 503):
            response["headers"] = {"Retry-After": copy.deepcopy(RETRY_AFTER_HEADER)}
        operation["responses"][str(status)] = response


def _referenced_schemas(
    root: Any, available: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Every schema ``root`` references, directly or through other schemas."""
    found: dict[str, dict[str, Any]] = {}
    pending = [root]
    while pending:
        node = pending.pop()
        if isinstance(node, list):
            pending.extend(node)
        elif isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith(_SCHEMA_REF):
                name = ref[len(_SCHEMA_REF) :]
                if name not in found:
                    found[name] = available[name]
                    pending.append(found[name])
            pending.extend(node.values())
    return found


def render(document: dict[str, Any]) -> str:
    """The canonical file text: sorted keys, two-space indent, one trailing newline."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def load_snapshot(path: Path | str | None = None) -> dict[str, Any]:
    return json.loads(Path(path or SNAPSHOT_PATH).read_text(encoding="utf-8"))


def diff(expected: Any, actual: Any, pointer: str = "") -> list[str]:
    """Readable differences between two JSON values, one line per JSON pointer."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        lines: list[str] = []
        for key in sorted(expected.keys() | actual.keys()):
            child = f"{pointer}/{_escape(key)}"
            if key not in actual:
                lines.append(f"removed {child}")
            elif key not in expected:
                lines.append(f"added   {child}")
            else:
                lines.extend(diff(expected[key], actual[key], child))
        return lines
    if expected != actual:
        before = json.dumps(expected, sort_keys=True)[:120]
        after = json.dumps(actual, sort_keys=True)[:120]
        return [f"changed {pointer or '/'}: {before} -> {after}"]
    return []


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def contract_app() -> FastAPI:
    """An app built with fixed settings, so the environment cannot change the contract."""
    from backend.config import Settings
    from backend.main import create_app

    settings = Settings(
        environment="test",
        api_prefix="/api/v1",
        database_url="sqlite:///:memory:",
        jwt_secret="contract-" * 6,
        token_pepper="contract-" * 6,
        _env_file=None,
    )
    return create_app(settings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check or regenerate the ingestion contract.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="fail when the snapshot is stale")
    action.add_argument("--write", action="store_true", help="regenerate the snapshot")
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT_PATH)
    args = parser.parse_args(argv)

    text = render(build_contract(contract_app()))
    if args.write:
        args.snapshot.write_text(text, encoding="utf-8")
        print(f"Wrote {args.snapshot}")
        return 0

    current = args.snapshot.read_text(encoding="utf-8") if args.snapshot.exists() else ""
    if current == text:
        print(f"{args.snapshot} is up to date.")
        return 0
    print(f"{args.snapshot} differs from the live ingestion contract:")
    lines = diff(json.loads(current), json.loads(text)) if current else ["the file is missing"]
    for line in lines[:50] or ["only the formatting differs"]:
        print(f"  {line}")
    if len(lines) > 50:
        print(f"  ... and {len(lines) - 50} more")
    print(f"If the change is deliberate, run from Homayar/: {WRITE_COMMAND}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
