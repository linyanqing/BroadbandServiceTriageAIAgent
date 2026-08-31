"""Node 4: observation -- normalizes the last tool result into agent state."""

from __future__ import annotations

from ..state import AgentState


def observation_node(state: AgentState) -> dict:
    last = state["tool_results"][-1]
    if last["error"]:
        return {
            "observations": [{"tool": last["tool"], "result": {"error": last["error"]}}],
        }

    observation = {"tool": last["tool"], "result": last["output"]}
    updates: dict = {"observations": [observation]}

    if last["tool"] == "get_broadband_service":
        updates["service_id"] = last["output"].get("service_id")
    elif last["tool"] == "create_fault_ticket":
        updates["ticket_id"] = last["output"].get("ticket_id")

    return updates
