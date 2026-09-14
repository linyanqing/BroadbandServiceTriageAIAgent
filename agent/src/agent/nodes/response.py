"""Node: final_response -- customer-facing text, grounded only in evidence.

Deliberately template-based rather than another LLM call: every value it
fills in (region, diagnosis, ticket_id, ...) is read back out of
tool_results, and the resolution itself is read back out of
specialist_results (never trusted straight from orchestrator_action, even
if a policy set one) -- so the response can never state something no
specialist actually observed. This is the supervisor-level analogue of the
same grounding rule each specialist's own DecisionPolicy already follows.
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import AgentConfig
from ..state import AgentState

_VALID_SPECIALISTS = {"network_diagnose", "billing", "line_testing", "equipment_reset"}

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
    "billing_hold_resolved": (
        "We found an overdue balance of ${overdue_amount:.2f} on your account and "
        "applied a credit (ref {credit_id}) to resolve it. Your service should be "
        "restored shortly."
    ),
    "billing_no_issue_found": (
        "We checked your billing status and found no overdue balance or issue "
        "affecting your service."
    ),
    "line_test_fault_confirmed": (
        "Our remote line test detected a fault on your physical line. We've "
        "scheduled a technician visit (ref {visit_id}, {scheduled_window})."
    ),
    "line_test_passed": ("Our remote line test shows your physical line is performing normally."),
    "equipment_reset_completed": (
        "We remotely reset your equipment (ref {reset_id}). Please allow a few "
        "minutes for it to reconnect."
    ),
    "equipment_reset_not_required": (
        "We checked your equipment and it's operating normally -- no reset was needed."
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


def make_final_response_node(config: AgentConfig) -> Callable:
    def node(state: AgentState) -> dict:
        action = state.get("orchestrator_action") or {}
        results = state.get("specialist_results", [])

        if action.get("type") == "dispatch" and action.get("specialist") not in _VALID_SPECIALISTS:
            resolution = "escalated_invalid_action"
            diagnostic_summary = (
                f"Model attempted to dispatch to unknown specialist '{action.get('specialist')}'."
            )
        elif results:
            last = results[-1]
            resolution = last["resolution"]
            diagnostic_summary = last["diagnostic_summary"]
        else:
            resolution = action.get("resolution") or "escalated_error"
            diagnostic_summary = action.get("message") or action.get("diagnostic_summary")

        if state.get("specialist_dispatch_count", 0) > config.max_specialist_dispatches:
            resolution = "escalated_timeout"
        if resolution is None:
            resolution = "escalated_error"

        outage = _find_result(state, "check_outage")
        diagnostics = _find_result(state, "run_network_diagnostics")
        ticket = _find_result(state, "create_fault_ticket")
        billing_status = _find_result(state, "check_billing_status")
        credit = _find_result(state, "apply_billing_credit")
        visit = _find_result(state, "schedule_technician_visit")
        reset = _find_result(state, "trigger_equipment_reset")

        template = _TEMPLATES.get(resolution, _TEMPLATES["escalated_error"])
        text = template.format(
            region=outage.get("region", "your area"),
            diagnosis=diagnostics.get("diagnosis", "an unspecified fault"),
            ticket_id=ticket.get("ticket_id") or state.get("ticket_id") or "",
            overdue_amount=billing_status.get("overdue_amount", 0.0),
            credit_id=credit.get("credit_id") or "",
            visit_id=visit.get("visit_id") or "",
            scheduled_window=visit.get("scheduled_window", "soon"),
            reset_id=reset.get("reset_id") or "",
        )

        status = "escalated" if resolution.startswith("escalated") else "resolved"

        return {
            "final_response": text,
            "status": status,
            "resolution": resolution,
            "diagnostic_summary": diagnostic_summary,
            "ticket_id": ticket.get("ticket_id", state.get("ticket_id")),
        }

    return node
