"""Tool 3: check_outage -- mocked Outage Service."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry, ToolSpec

# Scenario fixture: service BB-456 (customer 456) is used to demonstrate the
# "known outage" path (scenario B), where diagnostics must NOT be run.
_OUTAGE_FIXTURES: dict[str, dict] = {
    "BB-456": {"outage": True, "region": "Melbourne"},
}
_DEFAULT_REGION = "Sydney"


class CheckOutageInput(BaseModel):
    model_config = ConfigDict(title="check_outage")

    service_id: str = Field(description="The broadband service identifier")


def check_outage(payload: CheckOutageInput) -> dict:
    fixture = _OUTAGE_FIXTURES.get(payload.service_id)
    if fixture:
        return {
            "outage": fixture["outage"],
            "region": fixture["region"],
            "status": "outage_detected" if fixture["outage"] else "normal",
        }
    return {"outage": False, "region": _DEFAULT_REGION, "status": "normal"}


def register_outage_tool(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="check_outage",
            description="Check whether there is a known network outage affecting a service_id.",
            input_schema=CheckOutageInput,
            risk="low",
            handler=check_outage,
        )
    )
