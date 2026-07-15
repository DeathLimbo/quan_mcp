"""Structured JSON logging via structlog. All logs carry trace_id / request_id."""
from __future__ import annotations

import logging
import os
import sys
from contextvars import ContextVar
from typing import Any

import structlog

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def _inject_trace(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    tid = _trace_id.get()
    rid = _request_id.get()
    if tid:
        event_dict.setdefault("trace_id", tid)
    if rid:
        event_dict.setdefault("request_id", rid)
    return event_dict


def configure_logging(level: str | None = None) -> None:
    lvl = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, lvl, logging.INFO),
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _inject_trace,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, lvl, logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def bind_trace(trace_id: str | None = None, request_id: str | None = None) -> None:
    if trace_id is not None:
        _trace_id.set(trace_id)
    if request_id is not None:
        _request_id.set(request_id)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name or "quant")
