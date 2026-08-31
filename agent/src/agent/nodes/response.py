"""Node 6: final_response -- customer-facing text, grounded only in evidence.

Deliberately template-based rather than another LLM call: every value it
fills in (region, diagnosis, ticket_id) is read back out of tool_results, so
the response can never state something the tools didn't actually observe.
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import AgentConfig
from ..state import AgentState
from ..tools.registry import ToolRegistry

_TEMPLATES = {
    "known_outage": (
        "There is a known outage affecting your service in {region}. Our network "
        "team is already restoring service in your area, so no further action is "
        "needed from you right now."
    ),
    "fault_ticket_created": (
        "We ran diagnostics on your broadband service and found a fault "
        "({diagnosis}). We've created ticket {ticket_id} and a technician will "
        "follow up."
    ),
    "healthy_no_action": (
        "Your broadband service is currently showing normal diagnostics (no "
        "significant packet loss or latency detected). If the drop-outs continue, "
        "please power-cycle your router and let us know if the issue persists."
    ),
    "escalated_error": (
        "We weren't able to complete automated diagnostics for your service due "
        "to an internal error. This has been escalated to our support team for "
        "manual review."
    ),
    "escalated_invalid_action": (
        "We weren't able to safely complete this request through automated "
        "diagnostics. This has been escalated to our support team for manual "
        "review."
    ),
    "escalated_timeout": (
        "This investigation is taking longer than expected. It has been escalated "
        "to our support team for manual review."
    ),
}


def _find_result(state: AgentState, tool_name: str) -> dict:
    for result in reversed(state.get("tool_results", [])):
        if result["tool"] == tool_name and not result.get("error"):
            return result["output"]
    return {}


def make_final_response_node(registry: ToolRegistry, config: AgentConfig) -> Callable:
    def node(state: AgentState) -> dict:
        action = state.get("current_action") or {}
        resolution = action.get("resolution")

        if action.get("type") == "tool" and registry.get(action.get("tool")) is None:
            resolution = "escalated_invalid_action"
        if state.get("iteration_count", 0) > config.max_iterations:
            resolution = "escalated_timeout"
        if resolution is None:
            resolution = "escalated_error"

        outage = _find_result(state, "check_outage")
        diagnostics = _find_result(state, "run_network_diagnostics")
        ticket = _find_result(state, "create_fault_ticket")

        template = _TEMPLATES.get(resolution, _TEMPLATES["escalated_error"])
        text = template.format(
            region=outage.get("region", "your area"),
            diagnosis=diagnostics.get("diagnosis", "an unspecified fault"),
            ticket_id=ticket.get("ticket_id") or state.get("ticket_id") or "",
        )

        status = "escalated" if resolution.startswith("escalated") else "resolved"

        return {
            "final_response": text,
            "status": status,
            "resolution": resolution,
            "diagnostic_summary": action.get("diagnostic_summary", state.get("diagnostic_summary")),
            "ticket_id": ticket.get("ticket_id", state.get("ticket_id")),
        }

    return node
