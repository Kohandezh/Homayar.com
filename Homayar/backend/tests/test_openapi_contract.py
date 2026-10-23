"""HOM-85 (WP16-T01): the published ingestion contract matches the live app.

The snapshot ``backend/contracts/ingestion-v1.openapi.json`` is the public
machine contract of the asynchronous ingestion API. These tests pin its shape
(exact operations, resolvable references, bearer auth, the shared error
envelope) and fail when the live schema of an in-scope route drifts from it.
"""

from __future__ import annotations

import copy
import json

import pytest

from backend.contracts import openapi_contract as contract
from backend.main import create_app

EXPECTED_OPERATIONS = {
    ("post", "/api/v1/uploads"),
    ("post", "/api/v1/voicemails"),
    ("get", "/api/v1/meetings/{meeting_id}"),
    ("get", "/api/v1/meetings/{meeting_id}/job"),
    ("get", "/api/v1/ingestions"),
    ("get", "/api/v1/ingestions/{ingestion_id}"),
    ("get", "/api/v1/meetings/{meeting_id}/transcript"),
    ("get", "/api/v1/meetings/{meeting_id}/summary"),
    ("get", "/api/v1/meetings/{meeting_id}/actions"),
    ("get", "/api/v1/meetings/{meeting_id}/evidence"),
    ("get", "/api/v1/meetings/{meeting_id}/quality"),
    ("get", "/api/v1/meetings/{meeting_id}/export"),
    # HOM-82: the role-scoped trend views (contract 1.3.0)
    ("get", "/api/v1/trends/teams"),
    ("get", "/api/v1/trends/me"),
    ("get", "/api/v1/organizations/{organization_id}/trends/issues"),
    ("get", "/api/v1/organizations/{organization_id}/workspaces/{workspace_id}/trends/issues"),
}
HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
ERROR_REF = "#/components/schemas/ErrorResponse"


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def snapshot() -> dict:
    return contract.load_snapshot()


def _operations(document: dict) -> set[tuple[str, str]]:
    return {
        (method, path)
        for path, item in document["paths"].items()
        for method in item
        if method in HTTP_METHODS
    }


def _refs(node) -> list[str]:
    if isinstance(node, dict):
        found = [node["$ref"]] if isinstance(node.get("$ref"), str) else []
        for value in node.values():
            found.extend(_refs(value))
        return found
    if isinstance(node, list):
        return [ref for item in node for ref in _refs(item)]
    return []


def _resolve(document: dict, ref: str):
    assert ref.startswith("#/"), ref
    node = document
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


# --- drift ------------------------------------------------------------------------


def test_snapshot_matches_the_live_app(app, snapshot):
    live = contract.build_contract(app)
    if live != snapshot:
        pytest.fail(
            "The live ingestion contract differs from the snapshot:\n"
            + "\n".join(contract.diff(snapshot, live)[:40])
            + f"\nIf the change is deliberate, run from Homayar/: {contract.WRITE_COMMAND}",
            pytrace=False,
        )
    # The file is in the canonical form, so --write on any machine gives the same bytes.
    assert contract.SNAPSHOT_PATH.read_text(encoding="utf-8") == contract.render(live), (
        f"Snapshot formatting is not canonical. Run from Homayar/: {contract.WRITE_COMMAND}"
    )


def test_contract_does_not_depend_on_runtime_settings(settings, snapshot):
    changed = settings.model_copy(
        update={
            "max_upload_size_mb": settings.max_upload_size_mb + 7,
            "api_key_rate_limit_per_hour": 3,
            "upload_rate_limit_per_hour": 2,
            "environment": "development",
        }
    )
    assert contract.build_contract(create_app(changed)) == snapshot
    # The CLI builds its own app; it must agree with the test fixture's app.
    assert contract.build_contract(contract.contract_app()) == snapshot


def test_contract_reads_the_live_schema(app, snapshot):
    live = app.openapi()  # cached on the app after this call
    live["components"]["schemas"]["JobResponse"]["properties"]["hom85_probe"] = {"type": "string"}
    changed = contract.build_contract(app)
    assert changed != snapshot
    assert any("hom85_probe" in line for line in contract.diff(snapshot, changed))


def _route(app, path: str, method: str):
    return next(
        route
        for route in app.routes
        if getattr(route, "path", None) == path and method in getattr(route, "methods", ())
    )


def test_a_status_code_change_on_an_in_scope_route_changes_the_contract(app, snapshot):
    route = _route(app, "/api/v1/meetings/{meeting_id}/job", "GET")
    route.status_code = 201
    app.openapi_schema = None
    assert contract.build_contract(app) != snapshot


def test_a_route_level_error_the_table_also_names_makes_the_build_fail(app):
    # The table must never hide what a route documents itself.
    route = _route(app, "/api/v1/uploads", "POST")
    route.responses = {**route.responses, 409: {"description": "Conflict, from the route"}}
    app.openapi_schema = None
    with pytest.raises(LookupError, match=r"POST /api/v1/uploads .*409"):
        contract.build_contract(app)


def test_a_live_header_on_a_table_status_makes_the_build_fail(app):
    job = app.openapi()["paths"]["/api/v1/meetings/{meeting_id}/job"]["get"]
    job["responses"]["429"] = {
        "description": "Rate limited",
        "headers": {"Retry-After": {"schema": {"type": "integer"}}},
    }
    with pytest.raises(LookupError, match=r"GET /api/v1/meetings/\{meeting_id\}/job .*429"):
        contract.build_contract(app)


def test_a_custom_422_on_the_route_makes_the_build_fail(app):
    summary = app.openapi()["paths"]["/api/v1/meetings/{meeting_id}/summary"]["get"]
    summary["responses"]["422"]["description"] = "Custom validation answer"
    with pytest.raises(LookupError, match=r"422"):
        contract.build_contract(app)


def test_a_route_level_error_outside_the_table_reaches_the_contract(app, snapshot):
    route = _route(app, "/api/v1/meetings/{meeting_id}/job", "GET")
    route.responses = {**route.responses, 418: {"description": "Teapot, from the route"}}
    app.openapi_schema = None
    changed = contract.build_contract(app)
    assert changed != snapshot
    job = changed["paths"]["/api/v1/meetings/{meeting_id}/job"]["get"]
    assert job["responses"]["418"] == {"description": "Teapot, from the route"}


def test_a_documented_export_response_makes_the_build_fail(app):
    export = app.openapi()["paths"]["/api/v1/meetings/{meeting_id}/export"]["get"]
    export["responses"]["200"]["content"] = {"text/csv": {"schema": {"type": "string"}}}
    with pytest.raises(LookupError, match=r"GET /api/v1/meetings/\{meeting_id\}/export .*200"):
        contract.build_contract(app)


def test_an_out_of_scope_route_does_not_change_the_contract(app, snapshot):
    @app.get("/api/v1/hom85-out-of-scope-probe")
    def probe() -> dict:
        return {}

    app.openapi_schema = None
    assert "/api/v1/hom85-out-of-scope-probe" in app.openapi()["paths"]
    assert contract.build_contract(app) == snapshot


def test_build_contract_does_not_mutate_the_app_schema(app):
    before = copy.deepcopy(app.openapi())
    contract.build_contract(app)
    assert app.openapi() == before


def test_check_cli_passes_on_the_snapshot_and_fails_on_a_stale_one(tmp_path, capsys):
    assert contract.main(["--check"]) == 0

    stale = contract.load_snapshot()
    del stale["paths"]["/api/v1/meetings/{meeting_id}/job"]
    stale_path = tmp_path / "stale.openapi.json"
    stale_path.write_text(contract.render(stale), encoding="utf-8")
    capsys.readouterr()
    assert contract.main(["--check", "--snapshot", str(stale_path)]) != 0
    output = capsys.readouterr().out
    assert "/paths/~1api~1v1~1meetings~1{meeting_id}~1job" in output
    assert "--write" in output


def test_write_cli_regenerates_the_snapshot(tmp_path):
    target = tmp_path / "fresh.openapi.json"
    assert contract.main(["--write", "--snapshot", str(target)]) == 0
    assert target.read_bytes() == contract.SNAPSHOT_PATH.read_bytes()
    assert contract.main(["--check", "--snapshot", str(target)]) == 0


# --- shape of the published document -----------------------------------------------


def test_snapshot_is_openapi_3_with_the_contract_version_and_exact_operations(snapshot):
    assert snapshot["openapi"].startswith("3.")
    assert snapshot["info"]["version"] == "1.3.0"
    assert _operations(snapshot) == EXPECTED_OPERATIONS
    raw = contract.SNAPSHOT_PATH.read_text(encoding="utf-8")
    assert raw.endswith("}\n") and not raw.endswith("\n\n")


def test_every_ref_resolves_inside_the_file_and_no_schema_is_unused(snapshot):
    refs = set(_refs(snapshot))
    assert refs
    for ref in refs:
        _resolve(snapshot, ref)
    used = {ref.rsplit("/", 1)[1] for ref in refs if ref.startswith("#/components/schemas/")}
    assert set(snapshot["components"]["schemas"]) == used


def test_a_bearer_scheme_exists_and_every_operation_requires_it(snapshot):
    schemes = snapshot["components"]["securitySchemes"]
    bearer = [
        name for name, scheme in schemes.items() if scheme == {"type": "http", "scheme": "bearer"}
    ]
    assert bearer
    top = snapshot.get("security")
    for method, path in EXPECTED_OPERATIONS:
        security = snapshot["paths"][path][method].get("security", top)
        assert security and any(set(entry) & set(bearer) for entry in security), (method, path)


def test_upload_operation_documents_202_idempotency_errors_and_retry_after(snapshot):
    upload = snapshot["paths"]["/api/v1/uploads"]["post"]
    responses = upload["responses"]
    assert "202" in responses and "200" not in responses
    header = [
        parameter
        for parameter in upload["parameters"]
        if parameter["in"] == "header" and parameter["name"] == "Idempotency-Key"
    ]
    assert len(header) == 1 and header[0]["required"] is True
    for status in ("400", "401", "403", "409", "422", "429", "503"):
        assert status in responses, status
    for status in ("429", "503"):
        assert "Retry-After" in responses[status]["headers"], status


def test_every_error_response_uses_the_shared_error_envelope(snapshot):
    for method, path in EXPECTED_OPERATIONS:
        for status, response in snapshot["paths"][path][method]["responses"].items():
            if status[0] in "45":
                schema = response["content"]["application/json"]["schema"]
                assert schema == {"$ref": ERROR_REF}, (method, path, status)
    envelope = _resolve(snapshot, ERROR_REF)
    assert envelope["required"] == ["error"]
    error = envelope["properties"]["error"]
    if "$ref" in error:
        error = _resolve(snapshot, error["$ref"])
    assert {"code", "message", "request_id"} <= set(error["required"])


def test_descriptions_explain_auth_idempotency_retry_after_and_the_job_flow(snapshot):
    text = json.dumps(
        [snapshot["info"]]
        + [snapshot["paths"][path][method] for method, path in EXPECTED_OPERATIONS]
    )
    for phrase in ("Authorization: Bearer", "Idempotency-Key", "409", "Retry-After", "202"):
        assert phrase in text, phrase
    assert "/job" in snapshot["info"]["description"]


def test_the_shared_error_envelope_matches_real_error_bodies(client):
    response = client.get(
        "/api/v1/meetings/missing/job", headers={"Authorization": "Bearer hk_00000000.nope"}
    )
    assert response.status_code == 401
    body = response.json()
    assert set(body) == {"error"}
    assert {"code", "message", "request_id"} <= set(body["error"])
    assert body["error"]["code"] == "api_key_invalid"
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
