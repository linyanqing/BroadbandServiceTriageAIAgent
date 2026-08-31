"""Node 2: agent_decision -- the core ReAct reasoning node.

Delegates to whichever DecisionPolicy the graph was built with (mock or
Bedrock); this node only shapes the policy's output into state updates and
annotates whether the proposed action is high-risk, for callers inspecting
state while the graph is paused at human_approval.
"""

from __future__ import annotations

from collections.abc import Callable

from ..models.policy import DecisionPolicy
from ..state import AgentState
from ..tools.registry import ToolRegistry


def make_agent_decision_node(policy: DecisionPolicy, registry: ToolRegistry) -> Callable:
    def node(state: AgentState) -> dict:
        action = policy.decide(state)
        requires_approval = False
        if action.get("type") == "tool":
            spec = registry.get(action.get("tool"))
            requires_approval = bool(spec and spec.risk == "high" and not state.get("approved"))
        return {
            "current_action": action,
            "iteration_count": state.get("iteration_count", 0) + 1,
            "requires_human_approval": requires_approval,
        }

    return node
