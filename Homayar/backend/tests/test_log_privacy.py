"""Log privacy: access log paths, exception summaries, Celery task failures (HOM-113 follow-up).

Every check here captures what a real component writes: uvicorn's access and
error loggers behind a real server, Celery's task-failure record from a real
task run, and the JSON handler on the process's stdout.
"""

from __future__ import annotations

import json
import logging
import time

import pytest
from fastapi.testclient import TestClient

from backend.observability import scrub
from backend.observability.access_log import (
    ACCESS_LOGGER,
    AccessLogFilter,
    install_access_log_filter,
    route_template_for,
)
from backend.observability.scrub import diagnostic_text
from backend.providers.base import ProviderError
from backend.services.logging_service import (
    JSONFormatter,
    exception_summary,
    safe_message,
)
from backend.storage.base import StorageError
from backend.tests.test_observability_metrics import (
    _EMAIL,
    _PHONE,
    _SHARE_TOKEN,
    _app_client,
    _uvicorn,
)

_INVITE_TOKEN = "inv_" + "Kp7" * 12


class _FakeDiag:
    constraint_name = "uq_users_email"
    table_name = "users"
    column_name = None


class FakePsycopgError(Exception):
    """A driver error the way psycopg raises it: SQLSTATE, diag and a DETAIL text."""

    sqlstate = "23505"
    diag = _FakeDiag()


def _driver_error() -> FakePsycopgError:
    return FakePsycopgError(
        'duplicate key value violates unique constraint "uq_users_email"\n'
        f"DETAIL:  Key (email)=({_EMAIL}) already exists."
    )


def _integrity_error():
    from sqlalchemy.exc import IntegrityError

    return IntegrityError(
        "INSERT INTO users (email, phone_e164) VALUES (%(email)s, %(phone)s)",
        {"email": _EMAIL, "phone": _PHONE},
        _driver_error(),
    )


def _validation_error():
    from pydantic import BaseModel, ValidationError

    class Contact(BaseModel):
        phone: int

    try:
        Contact(phone=f"call {_PHONE} or {_EMAIL}")
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a validation error")


def _wrapped() -> RuntimeError:
    try:
        raise _integrity_error()
    except Exception as cause:  # noqa: BLE001 - building a chained exception
        try:
            raise RuntimeError(f"saving {_EMAIL} failed: {cause}") from cause
        except RuntimeError as wrapper:
            return wrapper
    raise AssertionError("unreachable")


def _with_code(code: str) -> ValueError:
    error = ValueError(f"bad input {_EMAIL}")
    error.code = code  # type: ignore[attr-defined]
    return error


# --- exception summaries ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        (lambda: RuntimeError(f"cannot notify {_EMAIL} or {_PHONE}"), "RuntimeError"),
        (
            _driver_error,
            "FakePsycopgError sqlstate=23505 constraint_name=uq_users_email table_name=users",
        ),
        (
            _integrity_error,
            "IntegrityError FakePsycopgError sqlstate=23505 constraint_name=uq_users_email "
            "table_name=users",
        ),
        (_validation_error, "ValidationError"),
        (_wrapped, "RuntimeError cause=IntegrityError"),
        (lambda: OSError(28, "No space left on device", f"/data/{_EMAIL}"), "OSError errno=28"),
        (
            lambda: StorageError(f"object tenants/{_EMAIL}/a missing", code="object_not_found"),
            "StorageError code=object_not_found",
        ),
        (
            lambda: ProviderError("asr_timeout", "Speech recognition timed out"),
            "ProviderError code=asr_timeout: Speech recognition timed out",
        ),
        (
            lambda: ExceptionGroup("unhandled errors in a TaskGroup", [_integrity_error()]),
            "IntegrityError FakePsycopgError sqlstate=23505 constraint_name=uq_users_email "
            "table_name=users",
        ),
        (lambda: _with_code("x" * 65), "ValueError"),
    ],
    ids=[
        "runtime",
        "raw-driver",
        "sqlalchemy",
        "validation",
        "wrapper",
        "oserror",
        "storage-code",
        "provider-allow-listed",
        "exception-group",
        "unbounded-code",
    ],
)
def test_exception_summaries_name_the_class_and_bounded_fields_only(build, expected):
    summary = exception_summary(build())
    assert summary == expected
    for leaked in (_EMAIL, _PHONE, "INSERT INTO", "DETAIL", "tenants/"):
        assert leaked not in summary


def test_an_allow_listed_message_is_still_scrubbed():
    summary = exception_summary(ProviderError("asr_failed", f"rejected for {_EMAIL}"))
    assert summary == "ProviderError code=asr_failed: rejected for [EMAIL_1]"


def test_an_exception_passed_as_a_message_argument_is_replaced_by_its_summary():
    error = _integrity_error()
    record = logging.LogRecord(
        "homayar",
        logging.ERROR,
        __file__,
        1,
        "write failed: %s",
        (error,),
        (type(error), error, None),
    )
    line = json.loads(JSONFormatter().format(record))
    assert line["message"].startswith("write failed: IntegrityError FakePsycopgError")
    for leaked in (_EMAIL, _PHONE, "INSERT INTO", "DETAIL"):
        assert leaked not in json.dumps(line)


def test_safe_message_without_an_exception_is_only_scrubbed():
    record = logging.LogRecord("homayar", logging.INFO, __file__, 1, "sent to %s", (_EMAIL,), None)
    assert safe_message(record) == "sent to [EMAIL_1]"


# --- uvicorn's access log ---------------------------------------------------------------


def test_route_templates_replace_raw_paths_and_query_strings(settings):
    with _app_client(settings) as client:
        routes = client.app.router.routes
        assert route_template_for(routes, "GET", f"/api/v1/shares/{_SHARE_TOKEN}") == (
            "/api/v1/shares/{share_token}"
        )
        assert route_template_for(routes, "GET", f"/healthz?token={_SHARE_TOKEN}") == "/healthz"
        assert route_template_for(routes, "GET", f"/nowhere/{_SHARE_TOKEN}") == "unmatched"
        # The path exists, the method does not (405): still the template.
        assert route_template_for(routes, "DELETE", "/healthz") == "/healthz"


def test_the_access_log_of_a_real_server_has_templates_not_tokens(settings, capfd):
    from uvicorn.logging import AccessFormatter

    lines: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record):
            lines.append(self.format(record))

    capture = Capture()
    capture.setFormatter(
        AccessFormatter(
            '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            use_colors=False,
        )
    )
    access_logger = logging.getLogger(ACCESS_LOGGER)
    saved = access_logger.disabled, access_logger.level
    access_logger.disabled = False
    access_logger.setLevel(logging.INFO)  # uvicorn configures it so in production
    assert access_logger.isEnabledFor(logging.INFO)
    access_logger.addHandler(capture)
    try:
        with _app_client(settings) as client:
            import httpx

            with (
                _uvicorn(client.app, access_log=True) as port,
                httpx.Client(trust_env=False, timeout=30) as http,
            ):
                base = f"http://127.0.0.1:{port}"
                headers = {"Host": "testserver"}
                http.get(f"{base}/api/v1/shares/{_SHARE_TOKEN}", headers=headers)
                http.get(f"{base}/healthz?invite={_INVITE_TOKEN}", headers=headers)
                http.get(f"{base}/no-such/{_SHARE_TOKEN}", headers=headers)
    finally:
        access_logger.removeHandler(capture)
        access_logger.disabled, level = saved
        access_logger.setLevel(level)
    assert len(lines) == 3, lines
    assert '"GET /api/v1/shares/{share_token} HTTP/1.1"' in lines[0]
    assert '"GET /healthz HTTP/1.1"' in lines[1]
    assert '"GET unmatched HTTP/1.1"' in lines[2]
    captured = capfd.readouterr()
    for output in ("\n".join(lines), captured.out, captured.err):
        for leaked in (_SHARE_TOKEN, _INVITE_TOKEN):
            assert leaked not in output


def test_only_the_latest_apps_access_filter_is_attached(settings):
    with _app_client(settings) as client:
        install_access_log_filter(client.app.router.routes)
        install_access_log_filter(client.app.router.routes)
    filters = logging.getLogger(ACCESS_LOGGER).filters
    assert sum(isinstance(f, AccessLogFilter) for f in filters) == 1


def test_an_unexpected_access_record_shape_keeps_no_path():
    record = logging.LogRecord(
        ACCESS_LOGGER, logging.INFO, __file__, 1, "GET /api/v1/shares/%s", (_SHARE_TOKEN,), None
    )
    AccessLogFilter([]).filter(record)
    assert record.args == ()
    # Share tokens have no recognisable shape; the record is at least scrubbed.
    assert record.getMessage() == diagnostic_text(f"GET /api/v1/shares/{_SHARE_TOKEN}")


# --- Celery ------------------------------------------------------------------------------


@pytest.fixture
def celery_logging_restored():
    """Run Celery's worker logging set-up, then restore every bit of global state it touches.

    setup_logging_subsystem marks logging as set up (per app and class-wide), replaces the
    root handlers and exports _MP_FORK_* variables; left behind, a later in-process test
    worker (test_queue_routing) never finishes starting.
    """
    import os

    from celery.app.log import Logging

    from backend.celery_app import celery_app

    root = logging.getLogger()
    saved = (
        celery_app.log.already_setup,
        Logging._setup,
        list(root.handlers),
        root.level,
        {k: v for k, v in os.environ.items() if k.startswith("_MP_FORK_")},
    )
    celery_app.log.already_setup = False
    try:
        celery_app.log.setup_logging_subsystem(loglevel=logging.INFO)  # what a worker does
        yield celery_app
    finally:
        celery_app.log.already_setup, Logging._setup, handlers, level, env = saved
        root.handlers = handlers
        root.setLevel(level)
        for key in [k for k in os.environ if k.startswith("_MP_FORK_")]:
            del os.environ[key]
        os.environ.update(env)


def test_celery_workers_keep_the_scrubbed_json_logging(celery_logging_restored):
    root = logging.getLogger()
    formatters = [type(handler.formatter) for handler in root.handlers]
    assert JSONFormatter in formatters  # pytest's capture handlers may sit beside it
    assert not any(cls.__module__.startswith("celery") for cls in formatters)


def test_a_failed_celery_task_logs_its_summary_not_its_sql_or_pii(capfd, celery_logging_restored):
    celery_app = celery_logging_restored

    @celery_app.task(name="homayar.tests.explode_integrity")
    def explode_integrity():
        raise _integrity_error()

    trace_logger = logging.getLogger("celery.app.trace")
    trace_logger.disabled = False  # Alembic's fileConfig in other tests disables it
    capfd.readouterr()
    result = explode_integrity.apply(throw=False)
    assert result.failed()
    out, err = capfd.readouterr()
    failures = [
        json.loads(line)
        for line in out.splitlines()
        if line.startswith("{") and "raised unexpected" in line
    ]
    assert len(failures) == 1, out
    [failure] = failures
    assert failure["logger"] == "celery.app.trace"
    assert "homayar.tests.explode_integrity" in failure["message"]
    assert "IntegrityError FakePsycopgError sqlstate=23505" in failure["message"]
    assert failure["exception"].startswith("IntegrityError FakePsycopgError sqlstate=23505")
    for output in (out, err):
        for leaked in (_EMAIL, _PHONE, "INSERT INTO", "DETAIL", "Traceback"):
            assert leaked not in output, leaked


# --- the scrubber's input bound ---------------------------------------------------------


def test_a_quoted_secret_split_by_the_input_bound_does_not_survive():
    head = "postgresql://u:"
    tail = "@h {'password': 'correct horse battery staple and more'}"
    filler = scrub.MAX_SCRUB_INPUT - len(head) - len("@h {'password': 'correct horse batt")
    text = head + "p" * filler + tail
    assert text[: scrub.MAX_SCRUB_INPUT].endswith("batt")  # the bound splits the value
    result = diagnostic_text(text)
    for word in ("correct", "horse", "batt"):
        assert word not in result


@pytest.mark.parametrize(
    "hostile",
    [
        "a." * 8000,
        "eyJ-" * 4000,
        "x://" * 4000,
        "a://b:" * 2667,
        'password="x ' * 1334,
        "Bearer " * 2300,
        "a@b." * 4000,
        "token=" * 2667,
    ],
    ids=[
        "dots",
        "jwt-prefix",
        "schemes",
        "url-userinfo",
        "quoted-keys",
        "bearer",
        "at-signs",
        "key-value",
    ],
)
def test_scrubbing_hostile_input_of_the_full_bound_is_fast(hostile):
    assert len(hostile) >= scrub.MAX_SCRUB_INPUT
    started = time.monotonic()
    result = diagnostic_text(hostile)
    assert time.monotonic() - started < 1.0
    assert len(result) <= scrub.MAX_DIAGNOSTIC_LENGTH + len(scrub.TRUNCATED)


def test_the_unhandled_error_line_names_only_the_class(settings, caplog):
    from backend.services.logging_service import logger

    logger.disabled = False  # Alembic's fileConfig in other tests disables it
    with _app_client(settings) as client:
        app = client.app

        @app.get("/api/v1/log-privacy/explode")
        def explode():
            raise RuntimeError(f"cannot notify {_EMAIL} or {_PHONE}")

        failing = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level(logging.ERROR, logger="homayar"):
            assert failing.get("/api/v1/log-privacy/explode").status_code == 500
    [record] = [r for r in caplog.records if r.getMessage().startswith("UNHANDLED_ERROR")]
    line = json.loads(JSONFormatter().format(record))
    assert line["exception"] == "RuntimeError"


def test_an_exception_argument_without_exc_info_is_summarised():
    error = _integrity_error()
    for args in ((error,), ({"error": error},)):
        msg = "write failed: %s" if args[0] is error else "write failed: %(error)s"
        record = logging.LogRecord("homayar", logging.WARNING, __file__, 1, msg, args, None)
        line = JSONFormatter().format(record)
        assert "IntegrityError FakePsycopgError sqlstate=23505" in line
        for leaked in (_EMAIL, _PHONE, "INSERT INTO", "DETAIL", "[SQL", "[parameters"):
            assert leaked not in line


def test_a_celery_record_without_exc_info_drops_its_exception_text():
    context = {
        "name": "t",
        "id": "1",
        "description": "retry",
        "exc": repr(_integrity_error()),
        "traceback": "Traceback ...",
        "args": "()",
        "kwargs": "{}",
    }
    record = logging.LogRecord(
        "celery.app.trace",
        logging.INFO,
        __file__,
        1,
        "Task %(name)s[%(id)s] %(description)s: %(exc)s",
        (context,),
        None,
    )
    message = safe_message(record)
    assert message == "Task t[1] retry: [exception]"


def test_websocket_handshake_lines_never_carry_the_path():
    from backend.services.logging_service import ServerLogFilter

    for msg, args in (
        ('%s - "WebSocket %s" 403', ("1.2.3.4:5", f"/ws/{_SHARE_TOKEN}?t=x")),
        ('%s - "WebSocket %s" [accepted]', ("1.2.3.4:5", f"/ws/{_SHARE_TOKEN}")),
    ):
        record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, msg, args, None)
        ServerLogFilter().filter(record)
        assert _SHARE_TOKEN not in record.getMessage()
        assert '"WebSocket [path]"' in record.getMessage()


def test_formatting_a_record_never_emits_a_warning_that_would_be_logged_again():
    # A Celery worker routes warnings into logging (captureWarnings); a formatter that
    # warns (datetime.utcnow on Python 3.12) then recurses until the worker thread dies,
    # and an in-process test worker never finishes starting (PR #66 CI hang).
    import warnings

    record = logging.LogRecord("homayar", logging.INFO, __file__, 1, "ready", (), None)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning raised while formatting fails the test
        line = json.loads(JSONFormatter().format(record))
    assert line["timestamp"].endswith("Z") and "T" in line["timestamp"]


def test_a_record_logged_while_formatting_gets_a_minimal_line_not_recursion(monkeypatch):
    from backend.services import logging_service

    formatter = JSONFormatter()
    nested: list[str] = []
    original = logging_service.safe_message

    def logging_while_formatting(record):
        nested.append(
            formatter.format(
                logging.LogRecord(
                    "py.warnings", logging.WARNING, __file__, 1, "a warning", (), None
                )
            )
        )
        return original(record)

    monkeypatch.setattr(logging_service, "safe_message", logging_while_formatting)
    line = json.loads(
        formatter.format(logging.LogRecord("homayar", logging.INFO, __file__, 1, "ready", (), None))
    )
    assert line["message"] == "ready"
    assert json.loads(nested[0])["message"] == "[nested record]"


def test_a_formatting_error_never_raises_from_the_handler(monkeypatch):
    from backend.services import logging_service

    def broken(record):
        raise ValueError("scrubber failed")

    monkeypatch.setattr(logging_service, "safe_message", broken)
    line = json.loads(
        JSONFormatter().format(
            logging.LogRecord("homayar", logging.INFO, __file__, 1, "x", (), None)
        )
    )
    assert line["message"] == "[unformattable record]" and line["error_type"] == "ValueError"


def test_in_process_migrations_leave_the_logging_configuration_alone(settings):
    # alembic.ini's root level (WARNING) and console handler must not replace the
    # configuration of the process that runs the migrations (PR #66 CI: an Alembic
    # test silenced uvicorn's INFO access lines for every later test).
    from alembic import command
    from alembic.config import Config

    from backend.tests.test_observability_metrics import HOMAYAR_ROOT

    root = logging.getLogger()
    before = (root.level, list(root.handlers))
    config = Config(str(HOMAYAR_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(HOMAYAR_ROOT / "backend" / "migrations"))
    command.heads(config)  # loads env.py without touching a database
    assert (root.level, list(root.handlers)) == before
