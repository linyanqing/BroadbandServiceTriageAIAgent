"""Enterprise (vendor-neutral) telemetry via OpenTelemetry / OTLP.

This is deliberately separate from observability/langsmith.py: LangSmith is
AI-specific run/evaluation tracing, this module is the general
traces/metrics/logs pipeline that flows to the ADOT Collector -> Cribl ->
Datadog/Splunk (see docs/observability.md). The application never talks to
Datadog or Splunk directly -- only to the OTLP endpoint configured below,
which in a real deployment is the ADOT Collector sidecar.

Node/tool spans are created unconditionally (via `traced_step`) because a
tracer obtained before any provider is configured is a safe OpenTelemetry
no-op; only `setup_telemetry` decides whether a real OTLP pipeline is wired
up, so OTEL_ENABLED can be toggled without touching node code.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from langgraph.errors import GraphInterrupt
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from ..config import AgentConfig

_SERVICE_NAME = "broadband-triage-agent"

_state: dict[str, Any] = {"configured": False, "metrics": {}}


def setup_telemetry(config: AgentConfig) -> None:
    if not config.otel_enabled:
        _state["configured"] = False
        _state["metrics"] = _build_metrics(metrics.get_meter(_SERVICE_NAME))
        return

    resource = Resource.create(
        {
            "service.name": _SERVICE_NAME,
            "service.version": config.agent_version,
            "deployment.environment": config.environment,
        }
    )

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{config.otel_exporter_endpoint}/v1/traces")
        )
    )
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{config.otel_exporter_endpoint}/v1/metrics")
            )
        ],
    )
    metrics.set_meter_provider(meter_provider)

    _state["configured"] = True
    _state["metrics"] = _build_metrics(metrics.get_meter(_SERVICE_NAME))


def _build_metrics(meter: metrics.Meter) -> dict[str, Any]:
    return {
        "request_count": meter.create_counter(
            "triage.request.count", description="Triage API requests"
        ),
        "request_latency": meter.create_histogram(
            "triage.request.duration_ms", description="Triage API request latency"
        ),
        "tool_latency": meter.create_histogram(
            "triage.tool.duration_ms", description="Enterprise tool call latency"
        ),
        "error_count": meter.create_counter(
            "triage.error.count", description="Errors raised during triage"
        ),
        "agent_execution_duration": meter.create_histogram(
            "triage.agent.duration_ms", description="Full graph execution duration"
        ),
    }


def get_tracer() -> trace.Tracer:
    return trace.get_tracer(_SERVICE_NAME)


def get_meter_instruments() -> dict[str, Any]:
    if not _state["metrics"]:
        _state["metrics"] = _build_metrics(metrics.get_meter(_SERVICE_NAME))
    return _state["metrics"]


def is_configured() -> bool:
    return bool(_state["configured"])


@contextmanager
def traced_step(span_name: str, **attributes: Any):
    tracer = get_tracer()
    start = time.perf_counter()
    with tracer.start_as_current_span(span_name) as span:
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        try:
            yield span
        except GraphInterrupt:
            # not an error -- this is the human-in-the-loop pause control flow
            span.set_attribute("interrupted", True)
            raise
        except Exception as exc:  # re-raised after recording
            span.record_exception(exc)
            span.set_attribute("error", True)
            get_meter_instruments()["error_count"].add(1, {"span": span_name})
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            span.set_attribute("duration_ms", duration_ms)
            if span_name.startswith("tool."):
                get_meter_instruments()["tool_latency"].record(
                    duration_ms, {"tool": attributes.get("tool", "unknown")}
                )


def traced_node(node_name: str, fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
    """Wraps a LangGraph node callable in a `node.<name>` span."""

    def wrapped(state: dict) -> dict:
        with traced_step(f"node.{node_name}", node=node_name):
            return fn(state)

    return wrapped


def instrument_fastapi_app(app: Any, config: AgentConfig) -> None:
    if not config.otel_enabled:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)
