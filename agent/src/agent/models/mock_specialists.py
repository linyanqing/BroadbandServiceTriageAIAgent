"""Deterministic stand-ins for the billing, line-testing, and equipment-reset
specialist agents.

Same rationale as mock_policy.py's MockNetworkDiagnosePolicy: each of these
is a rule-based simulation of the same ReAct policy a live Bedrock-backed
specialist is expected to follow, reading only state["tool_results"] (via
the shared helpers in models/policy.py) and reacting to it -- not a scripted
call sequence. (context_gathering, unlike these four, needs no policy at
all -- see nodes/context_gathering.py.)
"""

from __future__ import annotations

from ..state import AgentState, CurrentAction
from ..tools.registry import ToolRegistry
from .policy import latest_error, successful_results_by_tool


def _rejected_or_error(state: AgentState) -> CurrentAction | None:
    if state.get("approval_rejected"):
        return {
            "type": "escalate",
            "resolution": "escalated_error",
            "message": "The high-risk action was not approved by a human reviewer.",
        }
    error = latest_error(state)
    if error:
        return {"type": "escalate", "resolution": "escalated_error", "message": error}
    return None


class MockBillingPolicy:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def decide(self, state: AgentState) -> CurrentAction:
        guard = _rejected_or_error(state)
        if guard:
            return guard

        results = successful_results_by_tool(state)
        service_id = state.get("service_id")

        if "check_billing_status" not in results:
            return {
                "type": "tool",
                "tool": "check_billing_status",
                "tool_input": {"service_id": service_id},
                "reasoning": "Check for an overdue balance or suspension.",
            }

        billing = results["check_billing_status"]["output"]
        if not billing.get("service_suspended"):
            return {
                "type": "complete",
                "resolution": "billing_no_issue_found",
                "diagnostic_summary": "No overdue balance found; billing is in good standing.",
            }

        if "apply_billing_credit" not in results:
            amount = billing.get("overdue_amount", 0.0)
            return {
                "type": "tool",
                "tool": "apply_billing_credit",
                "tool_input": {
                    "service_id": service_id,
                    "amount": amount,
                    "reason": (
                        f"Overdue balance of ${amount:.2f} "
                        f"({billing.get('days_overdue')} days) is suspending service."
                    ),
                },
                "reasoning": "Apply a credit to restore service.",
            }

        return {
            "type": "complete",
            "resolution": "billing_hold_resolved",
            "diagnostic_summary": "Applied a billing credit to resolve the suspension.",
        }


class MockLineTestingPolicy:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def decide(self, state: AgentState) -> CurrentAction:
        guard = _rejected_or_error(state)
        if guard:
            return guard

        results = successful_results_by_tool(state)
        service_id = state.get("service_id")

        if "run_remote_line_test" not in results:
            return {
                "type": "tool",
                "tool": "run_remote_line_test",
                "tool_input": {"service_id": service_id},
                "reasoning": "Run a remote line test before deciding on a technician visit.",
            }

        line_test = results["run_remote_line_test"]["output"]
        if line_test.get("result") != "line_fault_detected":
            return {
                "type": "complete",
                "resolution": "line_test_passed",
                "diagnostic_summary": "Line test shows normal sync speed and attenuation.",
            }

        if "schedule_technician_visit" not in results:
            return {
                "type": "tool",
                "tool": "schedule_technician_visit",
                "tool_input": {
                    "service_id": service_id,
                    "reason": (
                        f"Line test detected instability (sync={line_test.get('sync_speed_mbps')}Mbps, "
                        f"attenuation={line_test.get('attenuation_db')}dB)."
                    ),
                },
                "reasoning": "Schedule a technician visit for the confirmed line fault.",
            }

        return {
            "type": "complete",
            "resolution": "line_test_fault_confirmed",
            "diagnostic_summary": "Line fault confirmed; technician visit scheduled.",
        }


class MockEquipmentResetPolicy:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def decide(self, state: AgentState) -> CurrentAction:
        guard = _rejected_or_error(state)
        if guard:
            return guard

        results = successful_results_by_tool(state)
        service_id = state.get("service_id")

        if "run_equipment_diagnostics" not in results:
            return {
                "type": "tool",
                "tool": "run_equipment_diagnostics",
                "tool_input": {"service_id": service_id},
                "reasoning": "Check equipment health before deciding on a reset.",
            }

        diagnostics = results["run_equipment_diagnostics"]["output"]
        if diagnostics.get("recommendation") != "reset_required":
            return {
                "type": "complete",
                "resolution": "equipment_reset_not_required",
                "diagnostic_summary": "Equipment is online and up to date; no reset needed.",
            }

        if "trigger_equipment_reset" not in results:
            return {
                "type": "tool",
                "tool": "trigger_equipment_reset",
                "tool_input": {"service_id": service_id},
                "reasoning": "Reset outdated/stale equipment.",
            }

        return {
            "type": "complete",
            "resolution": "equipment_reset_completed",
            "diagnostic_summary": "Equipment was remotely reset.",
        }
