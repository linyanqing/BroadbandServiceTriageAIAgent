"""Tool 5: create_fault_ticket -- mocked Fault Management Service.

This is the POC's high-risk, policy-controlled action: the graph routes any
call to this tool through the human_approval node unless it has already been
approved (see graph.py's routing function and config.auto_approve_high_risk).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry, ToolSpec


class CreateFaultTicketInput(BaseModel):
    model_config = ConfigDict(title="create_fault_ticket")

    service_id: str = Field(description="The broadband service identifier")
    diagnostic_summary: str = Field(description="Grounded summary of the diagnosed fault")


def create_fault_ticket(payload: CreateFaultTicketInput) -> dict:
    ticket_number = 10000 + (sum(ord(c) for c in payload.service_id) % 90000)
    return {"ticket_id": f"INC-{ticket_number}", "status": "created"}


def register_fault_tool(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="create_fault_ticket",
            description="Create a fault ticket for a service_id, given a diagnostic_summary.",
            input_schema=CreateFaultTicketInput,
            risk="high",
            handler=create_fault_ticket,
        )
    )
