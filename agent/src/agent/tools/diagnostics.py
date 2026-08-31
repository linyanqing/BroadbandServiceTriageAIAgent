"""Tool 4: run_network_diagnostics -- mocked Network Diagnostic Service."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry, ToolSpec

# Scenario fixture: service BB-789 (customer 789) demonstrates the "healthy
# service" path (scenario C), where the agent gives guidance instead of
# raising a ticket. Every other service reports a recoverable line fault.
_HEALTHY_SERVICES = {"BB-789"}


class RunNetworkDiagnosticsInput(BaseModel):
    model_config = ConfigDict(title="run_network_diagnostics")

    service_id: str = Field(description="The broadband service identifier")


def run_network_diagnostics(payload: RunNetworkDiagnosticsInput) -> dict:
    if payload.service_id in _HEALTHY_SERVICES:
        return {
            "packet_loss": 0.1,
            "latency_ms": 18,
            "error_rate": 0.2,
            "diagnosis": "no_fault_detected",
        }
    return {
        "packet_loss": 12.4,
        "latency_ms": 85,
        "error_rate": 8.2,
        "diagnosis": "possible_line_fault",
    }


def register_diagnostics_tool(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="run_network_diagnostics",
            description="Run network diagnostics (packet loss, latency, error rate) on a service_id.",
            input_schema=RunNetworkDiagnosticsInput,
            risk="low",
            handler=run_network_diagnostics,
        )
    )
