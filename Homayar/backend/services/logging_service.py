"""Structured JSON logging for Homayar."""

import json
import logging
import sys
from datetime import datetime

from backend.observability.correlation import CorrelationLogFilter
from backend.observability.scrub import diagnostic_text


def exception_summary(exc: BaseException) -> str:
    """What a log line may say about an exception.

    A database error is named by its classes, SQLSTATE and the constraint, table
    and column involved: never by its message, which quotes the statement, the
    bound parameters and psycopg's DETAIL (row values: e-mails, phone numbers,
    content). Any other exception text is bounded and passes through the
    credential and personal-data scrubbers.
    """
    from sqlalchemy.exc import StatementError

    if isinstance(exc, StatementError):
        parts = [type(exc).__name__]
        orig = getattr(exc, "orig", None)
        if orig is not None:
            parts.append(type(orig).__name__)
            sqlstate = getattr(orig, "sqlstate", None)
            if sqlstate:
                parts.append(f"sqlstate={sqlstate}")
            diag = getattr(orig, "diag", None)
            for field in ("constraint_name", "table_name", "column_name"):
                value = getattr(diag, field, None) if diag is not None else None
                if value:
                    parts.append(f"{field}={value}")
        return " ".join(parts)
    return diagnostic_text(str(exc))


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
        message = diagnostic_text(record.getMessage()).strip()
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


class JSONFormatter(logging.Formatter):
    """One JSON object per record.

    ``correlation_id`` names the request, job attempt or sweep run the record
    belongs to (``backend.observability.correlation``). The message is bounded
    and passes through the credential and personal-data scrubbers; exceptions
    are summarised by ``exception_summary``. No traceback is written.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": diagnostic_text(record.getMessage()),
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
