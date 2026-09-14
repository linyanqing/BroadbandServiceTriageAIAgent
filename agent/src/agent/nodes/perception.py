"""Node 1: perception -- understands the customer request only.

Deliberately has no access to the tool registry or any enterprise system;
it only classifies the free-text message into a structured intent/context
that the supervisor (nodes/supervisor.py) uses to pick the first specialist.
"""

from __future__ import annotations

from ..state import AgentState

_DOWN_KEYWORDS = ("not working", "no internet", "can't connect", "cannot connect", "is down")
_INTERMITTENT_KEYWORDS = ("drop", "cutting out", "keeps disconnecting", "intermittent")
_BILLING_KEYWORDS = (
    "overcharged",
    "bill",
    "billing",
    "invoice",
    "payment",
    "charged",
    "refund",
    "credit",
)
_LINE_KEYWORDS = ("line noise", "crackling", "static on the line", "sync issue", "line test")
_EQUIPMENT_KEYWORDS = ("router", "modem", "reboot", "restart my", "reset my", "equipment")


def classify_issue(message: str) -> tuple[str, float]:
    # Technical keywords take priority over billing ones so a message
    # combining both ("keeps dropping out AND I've been overcharged")
    # dispatches network_diagnose first, with billing chained afterward via
    # billing_flagged below -- a purely billing-worded message still lands
    # on billing_dispute directly, since none of the technical checks match.
    lowered = message.lower()
    if any(keyword in lowered for keyword in _EQUIPMENT_KEYWORDS):
        return "equipment_issue", 0.85
    if any(keyword in lowered for keyword in _LINE_KEYWORDS):
        return "line_quality", 0.85
    if any(keyword in lowered for keyword in _DOWN_KEYWORDS):
        return "service_down", 0.9
    if any(keyword in lowered for keyword in _INTERMITTENT_KEYWORDS):
        return "intermittent_connection", 0.94
    if any(keyword in lowered for keyword in _BILLING_KEYWORDS):
        return "billing_dispute", 0.85
    return "general_connectivity", 0.6


def detect_billing_flagged(message: str) -> bool:
    """Independent of the primary issue_type -- lets the supervisor chain
    into the billing specialist after another one finishes, when the
    customer raised a billing concern alongside their primary complaint."""
    lowered = message.lower()
    return any(keyword in lowered for keyword in _BILLING_KEYWORDS)


def perception_node(state: AgentState) -> dict:
    message = state["customer_message"]
    issue_type, confidence = classify_issue(message)
    return {
        "intent": "broadband_troubleshooting",
        "issue_type": issue_type,
        "confidence": confidence,
        "billing_flagged": detect_billing_flagged(message),
        "status": "in_progress",
        "iteration_count": 0,
        "specialist_dispatch_count": 0,
    }
