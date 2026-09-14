"""Tools 10-11: run_equipment_diagnostics / trigger_equipment_reset -- mocked
Customer-Premises Equipment (CPE) Service, owned by the equipment-reset
specialist agent.

trigger_equipment_reset is unconditionally high-risk (like create_fault_ticket)
-- remotely rebooting a customer's router is disruptive and service-impacting
regardless of amount/context, so unlike apply_billing_credit it has no
risk_override.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry, ToolSpec

# Scenario fixture: service BB-333 (customer 333) demonstrates stale
# equipment that needs a reset.
_STALE_EQUIPMENT = {"BB-333"}


class RunEquipmentDiagnosticsInput(BaseModel):
    model_config = ConfigDict(title="run_equipment_diagnostics")

    service_id: str = Field(description="The broadband service identifier")


def run_equipment_diagnostics(payload: RunEquipmentDiagnosticsInput) -> dict:
    if payload.service_id in _STALE_EQUIPMENT:
        return {
            "device_online": True,
            "last_reboot_hours_ago": 340,
            "firmware_status": "outdated",
            "recommendation": "reset_required",
        }
    return {
        "device_online": True,
        "last_reboot_hours_ago": 6,
        "firmware_status": "current",
        "recommendation": "no_action",
    }


class TriggerEquipmentResetInput(BaseModel):
    model_config = ConfigDict(title="trigger_equipment_reset")

    service_id: str = Field(description="The broadband service identifier")


def trigger_equipment_reset(payload: TriggerEquipmentResetInput) -> dict:
    reset_number = 100 + (sum(ord(c) for c in payload.service_id) % 900)
    return {"reset_id": f"RST-{reset_number}", "status": "reset_triggered"}


def register_equipment_reset_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="run_equipment_diagnostics",
            description="Check a service_id's customer-premises equipment (router) health.",
            input_schema=RunEquipmentDiagnosticsInput,
            risk="low",
            handler=run_equipment_diagnostics,
        )
    )
    registry.register(
        ToolSpec(
            name="trigger_equipment_reset",
            description="Remotely reset a service_id's customer-premises equipment (router).",
            input_schema=TriggerEquipmentResetInput,
            risk="high",
            handler=trigger_equipment_reset,
        )
    )
