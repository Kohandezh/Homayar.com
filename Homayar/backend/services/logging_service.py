"""Structured JSON logging for Homayar."""
import json
import logging
import sys
from datetime import datetime

from backend.observability.correlation import CorrelationLogFilter
from backend.observability.scrub import scrub_text


class JSONFormatter(logging.Formatter):
    """One JSON object per record.

    ``correlation_id`` names the request, job attempt or sweep run the record
    belongs to (``backend.observability.correlation``). The message and the
    exception text pass through ``scrub_text`` so a credential that reaches a
    log argument (a URL with a password, an echoed header) is not written out.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": scrub_text(record.getMessage()),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id:
            log_entry["correlation_id"] = correlation_id
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = scrub_text(str(record.exc_info[1]))
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

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("neo4j").setLevel(logging.WARNING)
    logging.getLogger("passlib").setLevel(logging.WARNING)

    return logging.getLogger("homayar")


logger = setup_logging()
