"""Structured logging: one JSON object per event, with the request id on every line.

Logs carry identifiers, never content. A question, an answer and a retrieved chunk can all
contain personal or restricted text, and a log file is read by people who were never granted
the groups that made the retrieval legal in the first place. What goes in is what you need to
reconstruct a request: who asked (subject, not name), which route, how it ended, what it cost.

Local runs get a readable console renderer; everywhere else emits JSON for the log shipper.
"""

import logging
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

__all__ = ["bind_contextvars", "clear_contextvars", "configure_logging", "get_logger"]


def configure_logging(env: str = "local", level: int = logging.INFO) -> None:
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer = (
        structlog.dev.ConsoleRenderer() if env == "local" else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
