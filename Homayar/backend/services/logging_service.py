"""Structured JSON logging for Homayar."""

import json
import logging
import re
import sys
import threading
from collections.abc import Mapping
from datetime import UTC, datetime

from backend.observability.correlation import CorrelationLogFilter
from backend.observability.scrub import diagnostic_text

# Exception types whose message Homayar code writes for display and never fills
# with request input, personal data or a statement (by qualified class name, so
# this module imports none of them). Every other message is left out.
SAFE_MESSAGE_TYPES = frozenset(
    {
        "backend.providers.base.ProviderError",  # its public_message, by contract
        "backend.database.rls.RowLevelSecurityPostureError",  # deployment posture
    }
)
_BOUNDED_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_DIAGNOSTIC_FIELDS = ("constraint_name", "table_name", "column_name")


def _qualified(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _database_fields(error: BaseException) -> list[str]:
    """SQLSTATE and the constraint/table/column of a driver error (psycopg)."""
    fields = []
    sqlstate = getattr(error, "sqlstate", None)
    if isinstance(sqlstate, str) and _BOUNDED_CODE.fullmatch(sqlstate):
        fields.append(f"sqlstate={sqlstate}")
    diag = getattr(error, "diag", None)
    for field in _DIAGNOSTIC_FIELDS:
        value = getattr(diag, field, None) if diag is not None else None
        if isinstance(value, str) and _BOUNDED_CODE.fullmatch(value):
            fields.append(f"{field}={value}")
    return fields


def exception_summary(exc: BaseException) -> str:
    """What a log line may say about an exception: its class, never its text.

    Exception messages carry what the failing code had in hand: a database
    error quotes the statement, the bound parameters and psycopg's ``DETAIL``
    (row values: e-mails, phone numbers, content); a validation error repeats
    the input; a wrapper repeats the error it wraps. So a summary names the
    class and only fields that are bounded by construction: the driver error
    with its SQLSTATE and constraint/table/column, a machine-readable ``code``,
    an ``errno``, and the class of an explicit cause (``raise ... from``). The
    (scrubbed) message is added only for the types in ``SAFE_MESSAGE_TYPES``.
    """
    from sqlalchemy.exc import SQLAlchemyError, StatementError

    exc = _single_cause(exc)
    parts = [type(exc).__name__]
    if isinstance(exc, StatementError):
        orig = getattr(exc, "orig", None)
        if orig is not None:
            parts.append(type(orig).__name__)
            parts.extend(_database_fields(orig))
    else:
        parts.extend(_database_fields(exc))
        code = getattr(exc, "code", None)
        if not isinstance(exc, SQLAlchemyError) and isinstance(code, str):
            if _BOUNDED_CODE.fullmatch(code):
                parts.append(f"code={code}")
        if isinstance(exc, OSError) and isinstance(exc.errno, int):
            parts.append(f"errno={exc.errno}")
        # Only an explicit ``raise ... from``: the implicit context is often
        # unrelated (Starlette re-raises inside its exception-group handler).
        cause = _single_cause(exc.__cause__) if exc.__cause__ is not None else None
        if cause is not None and cause is not exc:
            parts.append(f"cause={type(cause).__name__}")
    summary = " ".join(parts)
    if any(_qualified(cls) in SAFE_MESSAGE_TYPES for cls in type(exc).__mro__):
        message = diagnostic_text(str(exc))
        if message:
            summary = f"{summary}: {message}"
    return summary


def _summarised(value: object) -> object:
    return exception_summary(value) if isinstance(value, BaseException) else value


def safe_message(record: logging.LogRecord) -> str:
    """The record's message with every embedded exception text replaced by its summary.

    An exception passed as a ``%s`` argument (tuple or mapping form, with or without
    ``exc_info``) is replaced by ``exception_summary``. Celery's task records pass
    ``repr(exception)`` as the ``exc`` argument (``... raised unexpected: %(exc)s``,
    also for Retry and expected ``throws=`` errors that carry no ``exc_info``): it
    becomes the summary, or ``[exception]`` when no exception object is attached.
    The result is also scrubbed.
    """
    exc = record.exc_info[1] if record.exc_info and record.exc_info[1] else None
    summary = exception_summary(exc) if exc is not None else None
    args = record.args
    message: str | None = None
    if isinstance(args, Mapping):
        safe_args = {key: _summarised(value) for key, value in args.items()}
        if "exc" in safe_args and not isinstance(args.get("exc"), BaseException):
            safe_args["exc"] = summary or "[exception]"
        if "traceback" in safe_args:
            safe_args["traceback"] = ""
        try:
            message = str(record.msg) % safe_args
        except (KeyError, TypeError, ValueError):
            message = None
    elif isinstance(args, tuple) and args:
        try:
            message = str(record.msg) % tuple(_summarised(value) for value in args)
        except (TypeError, ValueError):
            message = None
    if message is None:
        message = record.getMessage()
    if exc is not None and summary is not None:
        for text in {repr(exc), str(exc), repr(_single_cause(exc)), str(_single_cause(exc))}:
            if len(text) >= 8 and text != summary:
                message = message.replace(text, summary)
    return diagnostic_text(message)


# uvicorn's own loggers. ``uvicorn.error`` carries the server's records, among them
# "Exception in ASGI application" with the full traceback of an exception that
# escaped the app; its handler (plain text, stderr) is uvicorn's, not ours.
SERVER_LOGGERS = ("uvicorn", "uvicorn.error")
# Set on an exception by the API's unhandled-error handler, read when uvicorn logs it.
REQUEST_ID_ATTRIBUTE = "_homayar_request_id"


def _single_cause(exc: BaseException) -> BaseException:
    """The one exception inside nested single-member groups (anyio task groups)."""
    while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


def tag_exception(exc: BaseException, request_id: str) -> None:
    """Remember which request an exception belongs to (for the server's log line)."""
    for target in {exc, _single_cause(exc)}:
        try:
            setattr(target, REQUEST_ID_ATTRIBUTE, request_id)
        except (AttributeError, TypeError):
            pass


class ServerLogFilter(logging.Filter):
    """Make a uvicorn record safe before uvicorn's handler formats it (HOM-113).

    The message is scrubbed like ours and a traceback is replaced by
    ``exception_summary``: a database error's traceback quotes the statement,
    its bound parameters and psycopg's DETAIL. The request id the unhandled-error
    handler tagged on the exception becomes the record's correlation id.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            return self._filter(record)
        except Exception:  # noqa: BLE001 - a filter must never raise into the server
            record.msg, record.args = "[unfilterable record]", ()
            record.exc_info = record.exc_text = None
            return True

    def _filter(self, record: logging.LogRecord) -> bool:
        if '"WebSocket %s"' in str(record.msg) and isinstance(record.args, tuple):
            # uvicorn's handshake line carries the raw path and query string; the
            # app serves no WebSocket route, so the path is never worth keeping.
            record.args = tuple(
                "[path]" if index == 1 else value for index, value in enumerate(record.args)
            )
        message = safe_message(record).strip()
        exc = record.exc_info[1] if record.exc_info else None
        if exc is not None:
            exc = _single_cause(exc)
            request_id = getattr(exc, REQUEST_ID_ATTRIBUTE, None)
            if request_id:
                record.correlation_id = request_id
                message = f"{message} request_id={request_id}"
            message = f"{message} error_type={type(exc).__name__} error={exception_summary(exc)}"
        record.msg = message
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def install_server_log_filter() -> None:
    """Attach ``ServerLogFilter`` to uvicorn's loggers once (idempotent).

    uvicorn configures its loggers before it imports the app and keeps existing
    logger filters, so attaching at import and again at start-up covers both.
    """
    for name in SERVER_LOGGERS:
        server_logger = logging.getLogger(name)
        if not any(isinstance(f, ServerLogFilter) for f in server_logger.filters):
            server_logger.addFilter(ServerLogFilter())


_FORMATTING = threading.local()


class JSONFormatter(logging.Formatter):
    """One JSON object per record.

    ``correlation_id`` names the request, job attempt or sweep run the record
    belongs to (``backend.observability.correlation``). The message is bounded
    and passes through the credential and personal-data scrubbers; exceptions
    are summarised by ``exception_summary``. No traceback is written.
    """

    def format(self, record: logging.LogRecord) -> str:
        # A record emitted while formatting (a warning routed into logging, an error in a
        # scrubber) gets a minimal line instead of recursing (PR #66 CI hang).
        if getattr(_FORMATTING, "active", False):
            return json.dumps(
                {"level": record.levelname, "logger": record.name, "message": "[nested record]"}
            )
        _FORMATTING.active = True
        try:
            return self._format(record)
        except Exception as exc:  # noqa: BLE001 - a log line must never raise
            return json.dumps(
                {
                    "level": record.levelname,
                    "logger": record.name,
                    "message": "[unformattable record]",
                    "error_type": type(exc).__name__,
                }
            )
        finally:
            _FORMATTING.active = False

    def _format(self, record: logging.LogRecord) -> str:
        log_entry = {
            # Not datetime.utcnow(): it warns on Python 3.12, and with warnings captured
            # into logging (a Celery worker) every record would log another (recursion).
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": safe_message(record),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id:
            log_entry["correlation_id"] = correlation_id
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = exception_summary(record.exc_info[1])
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(level: str = "INFO"):
    """Configure structured JSON logging.

    The handler writes to the process's real stdout (``sys.__stdout__``). In
    a Celery worker ``sys.stdout`` is a logging proxy while tasks run; a root
    handler writing to it feeds records back into logging and Celery's
    recursion guard drops them, so every worker log after the first task
    (errors included) was silently lost."""
    handler = logging.StreamHandler(sys.__stdout__)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(CorrelationLogFilter())

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers = [handler]

    install_server_log_filter()

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("neo4j").setLevel(logging.WARNING)
    logging.getLogger("passlib").setLevel(logging.WARNING)

    return logging.getLogger("homayar")


logger = setup_logging()
