"""Deterministic stand-in for the supervisor's orchestrator policy.

Same rationale as models/mock_policy.py: a rule-based simulation of the same
dispatch policy a live Bedrock-backed supervisor is expected to follow,
reading only state (issue_type from perception, specialist_history/results
from whichever specialists have already run) -- not a scripted sequence.
Defaults to network_diagnose so the four originally-documented diagnostic
paths are completely unaffected by the new specialists existing.
"""

from __future__ import annotations

from ..state import AgentState, OrchestratorAction, SpecialistName

_ISSUE_TYPE_TO_SPECIALIST: dict[str, SpecialistName] = {
    "billing_dispute": "billing",
    "line_quality": "line_testing",
    "equipment_issue": "equipment_reset",
    "service_down": "network_diagnose",
    "intermittent_connection": "network_diagnose",
    "general_connectivity": "network_diagnose",
}


class MockOrchestratorPolicy:
    def decide(self, state: AgentState) -> OrchestratorAction:
        history = state.get("specialist_history", [])
        results = state.get("specialist_results", [])

        if results:
            last = results[-1]
            if last["resolution"].startswith("escalated"):
                # A dispatched specialist itself escalated (e.g. a rejected
                # approval) -- stop and surface that, don't try another one.
                return {"type": "complete"}
            if state.get("billing_flagged") and "billing" not in history:
                return {
                    "type": "dispatch",
                    "specialist": "billing",
                    "reasoning": "Customer also raised a billing concern.",
                }
            return {"type": "complete"}

        specialist = _ISSUE_TYPE_TO_SPECIALIST.get(state.get("issue_type"), "network_diagnose")
        return {
            "type": "dispatch",
            "specialist": specialist,
            "reasoning": f"Routing based on issue_type={state.get('issue_type')}.",
        }
