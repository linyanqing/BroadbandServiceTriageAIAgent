"""Builds one specialist agent's ReAct subgraph.

Every specialist (network_diagnose, billing, line_testing, equipment_reset)
shares this exact same internal shape -- agent_decision -> route ->
tool_execution -> observation -> (loop back, or human_approval -> loop
back) -> specialist_exit -- reusing the identical node factories the
original single-agent design already had (nodes/decision.py,
nodes/tool_execution.py, nodes/observation.py, nodes/approval.py). The
only genuinely new code per specialist is its own small DecisionPolicy
(models/mock_specialists.py, or models/bedrock.py reused with a scoped
registry) and its scoped ToolRegistry (tools/registry.py's
SPECIALIST_TOOL_SCOPES). See docs/agent-design.md.

context_gathering (the fixed, unconditional get_customer/get_broadband_service
step before any specialist is picked) deliberately does NOT use this shape
-- see nodes/context_gathering.py for why.

Each subgraph is compiled WITHOUT its own checkpointer -- the parent
graph's MemorySaver (graph.py) governs persistence/interrupts across the
whole investigation, including interrupt()s raised from inside a subgraph.
"""

from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .config import AgentConfig
from .models.policy import DecisionPolicy
from .nodes.approval import human_approval_node
from .nodes.decision import make_agent_decision_node
from .nodes.observation import observation_node
from .nodes.specialist_exit import make_specialist_exit_node
from .nodes.tool_execution import make_tool_execution_node
from .observability.telemetry import traced_node
from .state import AgentState, SpecialistName
from .tools.registry import ToolRegistry, effective_risk

RouteTarget = Literal["tool_execution", "human_approval", "specialist_exit"]


def make_specialist_router(registry: ToolRegistry, config: AgentConfig):
    """The exact routing logic graph.py's make_router used to have for the
    single-agent loop -- unchanged, just scoped to one specialist's
    registry and re-targeted (via the path_map at the call site) to that
    specialist's own exit node instead of the top-level final_response."""

    def route(state: AgentState) -> RouteTarget:
        if state.get("iteration_count", 0) > config.max_iterations:
            return "specialist_exit"

        action = state.get("current_action") or {}
        action_type = action.get("type")

        if action_type == "tool":
            spec = registry.get(action.get("tool"))
            if spec is None:
                return "specialist_exit"
            risk = effective_risk(spec, action.get("tool_input") or {})
            already_cleared = state.get("approved") or config.auto_approve_high_risk
            if risk == "high" and not already_cleared:
                return "human_approval"
            return "tool_execution"

        # complete, escalate, ask_customer -> all resolve through specialist_exit
        return "specialist_exit"

    return route


def build_specialist_subgraph(
    name: SpecialistName,
    policy: DecisionPolicy,
    registry: ToolRegistry,
    config: AgentConfig,
) -> CompiledStateGraph:
    graph = StateGraph(AgentState)
    graph.add_node(
        "agent_decision",
        traced_node(f"{name}.agent_decision", make_agent_decision_node(policy, registry, name)),
    )
    graph.add_node("tool_execution", make_tool_execution_node(registry))
    graph.add_node("observation", traced_node(f"{name}.observation", observation_node))
    graph.add_node("human_approval", traced_node(f"{name}.human_approval", human_approval_node))
    graph.add_node("specialist_exit", make_specialist_exit_node(name, registry, config))

    graph.add_edge(START, "agent_decision")
    graph.add_conditional_edges(
        "agent_decision",
        make_specialist_router(registry, config),
        {
            "tool_execution": "tool_execution",
            "human_approval": "human_approval",
            "specialist_exit": "specialist_exit",
        },
    )
    graph.add_edge("tool_execution", "observation")
    graph.add_edge("observation", "agent_decision")
    graph.add_edge("human_approval", "agent_decision")
    graph.add_edge("specialist_exit", END)

    return graph.compile()
