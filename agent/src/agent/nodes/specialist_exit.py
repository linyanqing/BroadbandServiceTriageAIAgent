"""Exit node for a specialist subgraph -- the specialist-local sibling of
nodes/response.py's top-level final_response.

Normalizes whatever the specialist's own current_action ended up being (a
grounded `complete`, a rogue-tool `escalate`, or an iteration-limit
timeout) into one SpecialistResult the supervisor can read, and resets the
transient approval/iteration state so the *next* dispatched specialist (or
context_gathering) starts clean -- see docs/agent-design.md for why this
reset has to happen here rather than in the supervisor.
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import AgentConfig
from ..state import AgentState, SpecialistName
from ..tools.registry import ToolRegistry


def make_specialist_exit_node(
    name: SpecialistName,
    registry: ToolRegistry,
    config: AgentConfig,
) -> Callable:
    def node(state: AgentState) -> dict:
        action = state.get("current_action") or {}
        resolution = action.get("resolution")

        if action.get("type") == "tool" and registry.get(action.get("tool")) is None:
            resolution = "escalated_invalid_action"
        if state.get("iteration_count", 0) > config.max_iterations:
            resolution = "escalated_timeout"
        if resolution is None:
            resolution = "escalated_error"

        return {
            # specialist-local state, reset for whichever specialist runs next
            "current_action": None,
            "approved": None,
            "approval_rejected": None,
            "requires_human_approval": False,
            "iteration_count": 0,
            "specialist_results": [
                {
                    "specialist": name,
                    "resolution": resolution,
                    "diagnostic_summary": action.get("diagnostic_summary"),
                }
            ],
            "specialist_history": [name],
        }

    return node
