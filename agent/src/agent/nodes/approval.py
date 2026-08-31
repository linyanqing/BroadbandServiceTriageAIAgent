"""Node 5: human_approval -- the pause/resume boundary for high-risk actions.

Uses LangGraph's `interrupt()` so the graph genuinely suspends execution
here; the API layer (main.py) surfaces the interrupt payload to the caller
and resumes the graph later via `Command(resume=...)` once a human decides.
"""

from __future__ import annotations

from langgraph.types import interrupt

from ..state import AgentState


def human_approval_node(state: AgentState) -> dict:
    action = state.get("current_action") or {}
    decision = interrupt(
        {
            "type": "approval_request",
            "tool": action.get("tool"),
            "tool_input": action.get("tool_input"),
            "reasoning": action.get("reasoning"),
            "request_id": state.get("request_id"),
        }
    )
    approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
    return {"approved": approved, "approval_rejected": not approved}
