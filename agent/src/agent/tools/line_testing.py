"""Tools 8-9: run_remote_line_test / schedule_technician_visit -- mocked
Line Test Service, owned by the line-testing specialist agent.

Distinct from network_diagnose's run_network_diagnostics: this checks the
physical copper/fibre line itself (sync speed, attenuation), not packet-level
network health -- a real carrier keeps these as separate backend systems.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry, ToolSpec

# Scenario fixture: service BB-222 (customer 222) demonstrates a confirmed
# line fault needing a technician visit.
_FAULTY_LINES = {"BB-222"}


class RunRemoteLineTestInput(BaseModel):
    model_config = ConfigDict(title="run_remote_line_test")

    service_id: str = Field(description="The broadband service identifier")


def run_remote_line_test(payload: RunRemoteLineTestInput) -> dict:
    if payload.service_id in _FAULTY_LINES:
        return {
            "sync_speed_mbps": 2.1,
            "attenuation_db": 58,
            "sync_status": "unstable",
            "result": "line_fault_detected",
        }
    return {
        "sync_speed_mbps": 95.0,
        "attenuation_db": 12,
        "sync_status": "stable",
        "result": "line_ok",
    }


class ScheduleTechnicianVisitInput(BaseModel):
    model_config = ConfigDict(title="schedule_technician_visit")

    service_id: str = Field(description="The broadband service identifier")
    reason: str = Field(description="Grounded reason for the visit, from the line test observed")


def schedule_technician_visit(payload: ScheduleTechnicianVisitInput) -> dict:
    visit_number = 100 + (sum(ord(c) for c in payload.service_id) % 900)
    return {"visit_id": f"VIS-{visit_number}", "scheduled_window": "next business day"}


def register_line_testing_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="run_remote_line_test",
            description="Run a remote line test (sync speed, attenuation) on a service_id.",
            input_schema=RunRemoteLineTestInput,
            risk="low",
            handler=run_remote_line_test,
        )
    )
    registry.register(
        ToolSpec(
            name="schedule_technician_visit",
            description="Schedule an on-site technician visit for a service_id, given a reason.",
            input_schema=ScheduleTechnicianVisitInput,
            risk="high",
            handler=schedule_technician_visit,
        )
    )
