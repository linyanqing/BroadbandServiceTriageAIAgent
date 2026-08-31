"""Structured JSON application logging with correlation-ID propagation.

This is the enterprise logging path (stdout -> ECS awslogs -> CloudWatch,
see docs/observability.md), independent of LangSmith and independent of the
OpenTelemetry trace/metric pipeline.
"""

from __future__ import annotations

import contextvars
import logging

from pythonjsonlogger.json import JsonFormatter

_correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)
_trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)


def set_correlation_context(request_id: str | None, trace_id: str | None) -> None:
    _correlation_id_var.set(request_id)
    _trace_id_var.set(trace_id)


class _CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id_var.get()
        record.trace_id = _trace_id_var.get()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter(
            "{asctime}{levelname}{name}{message}{correlation_id}{trace_id}",
            style="{",
        )
    )
    handler.addFilter(_CorrelationFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
