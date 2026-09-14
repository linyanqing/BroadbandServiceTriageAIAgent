"""Tool 1: get_customer -- mocked Customer Service API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry, ToolSpec

_CUSTOMER_FIXTURES: dict[str, dict] = {
    "123": {"name": "Test Customer", "status": "active"},
    "456": {"name": "Alex Nguyen", "status": "active"},
    "789": {"name": "Priya Singh", "status": "active"},
}


class GetCustomerInput(BaseModel):
    model_config = ConfigDict(title="get_customer")

    customer_id: str = Field(description="The customer's account identifier")


def get_customer(payload: GetCustomerInput) -> dict:
    fixture = _CUSTOMER_FIXTURES.get(
        payload.customer_id, {"name": f"Customer {payload.customer_id}", "status": "active"}
    )
    return {
        "customer_id": payload.customer_id,
        "name": fixture["name"],
        "status": fixture["status"],
    }


def register_customer_tool(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="get_customer",
            description="Retrieve the customer's account profile by customer_id.",
            input_schema=GetCustomerInput,
            risk="low",
            handler=get_customer,
        )
    )
