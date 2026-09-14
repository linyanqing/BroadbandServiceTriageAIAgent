"""Node: supervisor -- the top-level orchestrator that dispatches to one
specialist agent per turn (network_diagnose, billing, line_testing,
equipment_reset), or decides the whole investigation is complete.

This is the OrchestratorPolicy-level analogue of nodes/decision.py's
agent_decision node -- same shape, one level up, including the same
orchestrator_decision_cache guard against a confirmed LangGraph replay
behavior (see models/decision_cache.py and docs/agent-design.md's
"Interrupt replay and idempotency"). graph.py's make_supervisor_router
turns `orchestrator_action` into the actual edge choice, independently
re-validating the specialist name rather than trusting it (mirroring how
the specialist-level router never trusts a raw tool name either).
"""

from __future__ import annotations

from collections.abc import Callable

from ..models.decision_cache import compute_cache_key
from ..models.orchestrator_policy import OrchestratorPolicy
from ..state import AgentState


def _orchestrator_decision_cache_key(state: AgentState) -> str:
    return compute_cache_key(
        {
            "kind": "orchestrator_decision",
            "customer_id": state.get("customer_id"),
            "issue_type": state.get("issue_type"),
            "confidence": state.get("confidence"),
            "billing_flagged": state.get("billing_flagged", False),
            "specialist_history": state.get("specialist_history", []),
            "specialist_results": state.get("specialist_results", []),
        }
    )


def make_supervisor_node(policy: OrchestratorPolicy) -> Callable:
    def node(state: AgentState) -> dict:
        cache = state.get("orchestrator_decision_cache", {})
        key = _orchestrator_decision_cache_key(state)
        action = cache[key] if key in cache else policy.decide(state)
        return {
            "orchestrator_action": action,
            "specialist_dispatch_count": state.get("specialist_dispatch_count", 0) + 1,
            "orchestrator_decision_cache": {key: action},
        }

    return node
