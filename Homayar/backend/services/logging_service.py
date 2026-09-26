"""Structured JSON logging for Homayar."""
import logging
import json
import sys
from datetime import datetime


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])
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

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers = [handler]

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("neo4j").setLevel(logging.WARNING)
    logging.getLogger("passlib").setLevel(logging.WARNING)

    return logging.getLogger("homayar")


logger = setup_logging()
