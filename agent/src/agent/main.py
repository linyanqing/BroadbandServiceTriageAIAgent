"""FastAPI entry point: POST /api/v1/triage, GET /health, GET /ready.

`POST /api/v1/triage/{request_id}/approve` is an addition beyond the
minimum API surface in the spec, needed to actually drive the human-approval
resume demonstrated in scenario D (see docs/agent-design.md).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from langgraph.types import Command
from pydantic import BaseModel

from .config import AgentConfig, load_config
from .graph import build_graph
from .observability.langsmith import build_run_config
from .observability.logging import configure_logging, get_logger, set_correlation_context
from .observability.telemetry import (
    get_meter_instruments,
    instrument_fastapi_app,
    setup_telemetry,
    traced_step,
)
from .security.redaction import redact_dict

configure_logging()
logger = get_logger("agent.api")

config: AgentConfig = load_config()
setup_telemetry(config)
graph = build_graph(config)

app = FastAPI(title="Broadband Service Triage AI Agent", version=config.agent_version)
instrument_fastapi_app(app, config)


class TriageRequest(BaseModel):
    customer_id: str
    message: str


class ApprovalRequest(BaseModel):
    approved: bool


class TriageResponse(BaseModel):
    request_id: str
    status: str
    summary: str | None = None
    ticket_id: str | None = None
    resolution: str | None = None
    approval: dict[str, Any] | None = None


def _new_request_id() -> str:
    return f"REQ-{uuid.uuid4().hex[:8].upper()}"


def _extract_result(request_id: str, result: dict) -> TriageResponse:
    if "__interrupt__" in result:
        interrupt = result["__interrupt__"][0]
        payload = interrupt.value if hasattr(interrupt, "value") else interrupt
        return TriageResponse(
            request_id=request_id,
            status="awaiting_approval",
            summary="This request requires human approval before continuing.",
            approval=redact_dict(payload) if isinstance(payload, dict) else None,
        )
    return TriageResponse(
        request_id=request_id,
        status=result.get("status", "resolved"),
        summary=result.get("final_response"),
        ticket_id=result.get("ticket_id"),
        resolution=result.get("resolution"),
    )


@app.post("/api/v1/triage", response_model=TriageResponse)
def triage(payload: TriageRequest) -> TriageResponse:
    request_id = _new_request_id()
    trace_id = uuid.uuid4().hex
    set_correlation_context(request_id, trace_id)

    logger.info(
        "triage_request_received",
        extra={"request_id": request_id, "customer_id": payload.customer_id},
    )

    initial_state = {
        "customer_message": payload.message,
        "customer_id": payload.customer_id,
        "request_id": request_id,
        "trace_id": trace_id,
    }
    thread_config = {
        "configurable": {"thread_id": request_id},
        **build_run_config(initial_state, config),
    }

    start = time.perf_counter()
    metrics_ = get_meter_instruments()
    metrics_["request_count"].add(1, {"endpoint": "/api/v1/triage"})
    try:
        with traced_step("langgraph.execution", request_id=request_id):
            result = graph.invoke(initial_state, config=thread_config)
    except Exception:
        metrics_["error_count"].add(1, {"endpoint": "/api/v1/triage"})
        logger.exception("triage_request_failed", extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail="Triage investigation failed") from None
    finally:
        metrics_["request_latency"].record(
            (time.perf_counter() - start) * 1000, {"endpoint": "/api/v1/triage"}
        )

    response = _extract_result(request_id, result)
    logger.info(
        "triage_request_completed",
        extra={"request_id": request_id, "status": response.status},
    )
    return response


@app.post("/api/v1/triage/{request_id}/approve", response_model=TriageResponse)
def approve(request_id: str, payload: ApprovalRequest) -> TriageResponse:
    set_correlation_context(request_id, None)
    thread_config = {"configurable": {"thread_id": request_id}}

    snapshot = graph.get_state(thread_config)
    if not snapshot.interrupts:
        raise HTTPException(
            status_code=404, detail=f"No paused triage awaiting approval for '{request_id}'"
        )
    resume_config = {**thread_config, **build_run_config(snapshot.values, config)}

    try:
        with traced_step("langgraph.resume", request_id=request_id):
            result = graph.invoke(
                Command(resume={"approved": payload.approved}), config=resume_config
            )
    except Exception:
        logger.exception("triage_resume_failed", extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail="Failed to resume triage investigation") from None

    response = _extract_result(request_id, result)
    logger.info(
        "triage_resume_completed",
        extra={"request_id": request_id, "status": response.status, "approved": payload.approved},
    )
    return response


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict:
    return {"status": "ok", "mock_mode": config.mock_mode, "environment": config.environment}
