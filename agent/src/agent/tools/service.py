"""Tool 2: get_broadband_service -- mocked Service/Product Service."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry, ToolSpec


class GetBroadbandServiceInput(BaseModel):
    model_config = ConfigDict(title="get_broadband_service")

    customer_id: str = Field(description="The customer's account identifier")


def get_broadband_service(payload: GetBroadbandServiceInput) -> dict:
    return {
        "service_id": f"BB-{payload.customer_id}",
        "product": "Home Broadband",
        "technology": "FTTP",
        "status": "active",
    }


def register_service_tool(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="get_broadband_service",
            description="Retrieve the customer's broadband service details by customer_id.",
            input_schema=GetBroadbandServiceInput,
            risk="low",
            handler=get_broadband_service,
        )
    )
