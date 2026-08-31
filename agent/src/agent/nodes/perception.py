"""Node 1: perception -- understands the customer request only.

Deliberately has no access to the tool registry or any enterprise system;
it only classifies the free-text message into a structured intent/context.
"""

from __future__ import annotations

from ..state import AgentState

_DOWN_KEYWORDS = ("not working", "no internet", "can't connect", "cannot connect", "is down")
_INTERMITTENT_KEYWORDS = ("drop", "cutting out", "keeps disconnecting", "intermittent")


def classify_issue(message: str) -> tuple[str, float]:
    lowered = message.lower()
    if any(keyword in lowered for keyword in _DOWN_KEYWORDS):
        return "service_down", 0.9
    if any(keyword in lowered for keyword in _INTERMITTENT_KEYWORDS):
        return "intermittent_connection", 0.94
    return "general_connectivity", 0.6


def perception_node(state: AgentState) -> dict:
    issue_type, confidence = classify_issue(state["customer_message"])
    return {
        "intent": "broadband_troubleshooting",
        "issue_type": issue_type,
        "confidence": confidence,
        "status": "in_progress",
        "iteration_count": 0,
    }
