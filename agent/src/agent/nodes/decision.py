"""Node 2: agent_decision -- the core ReAct reasoning node.

Delegates to whichever DecisionPolicy the graph was built with (mock or
Bedrock); this node only shapes the policy's output into state updates and
annotates whether the proposed action is high-risk, for callers inspecting
state while the graph is paused at human_approval.

Checks decision_cache before calling policy.decide() -- see
models/decision_cache.py and docs/agent-design.md's "Interrupt replay and
idempotency" for why a specialist's decision, not just its tool calls,
needs this guard against a confirmed LangGraph replay behavior.
"""

from __future__ import annotations

from collections.abc import Callable

from ..models.decision_cache import compute_cache_key
from ..models.policy import DecisionPolicy
from ..state import AgentState, SpecialistName
from ..tools.registry import ToolRegistry, effective_risk


def _decision_cache_key(name: str, state: AgentState) -> str:
    return compute_cache_key(
        {
            "kind": "specialist_decision",
            "specialist": name,
            "customer_id": state.get("customer_id"),
            "service_id": state.get("service_id"),
            "observations": state.get("observations", []),
            "tool_results": state.get("tool_results", []),
            "approval_rejected": state.get("approval_rejected", False),
        }
    )


def make_agent_decision_node(
    policy: DecisionPolicy, registry: ToolRegistry, name: SpecialistName | str
) -> Callable:
    def node(state: AgentState) -> dict:
        cache = state.get("decision_cache", {})
        key = _decision_cache_key(name, state)
        action = cache[key] if key in cache else policy.decide(state)

        requires_approval = False
        if action.get("type") == "tool":
            spec = registry.get(action.get("tool"))
            risk = effective_risk(spec, action.get("tool_input") or {}) if spec else None
            requires_approval = bool(risk == "high" and not state.get("approved"))
        return {
            "current_action": action,
            "iteration_count": state.get("iteration_count", 0) + 1,
            "requires_human_approval": requires_approval,
            "decision_cache": {key: action},
        }

    return node
