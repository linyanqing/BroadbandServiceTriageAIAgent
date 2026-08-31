"""Builds the bounded LangGraph ReAct agent.

The graph fixes the allowed structure (which nodes exist, which transitions
are legal, where the human-approval boundary sits); the decision policy
plugged into `agent_decision` is what actually chooses the next action at
runtime. See docs/agent-design.md for the full rationale.
"""

from __future__ import annotations

from typing import Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .config import AgentConfig
from .models.bedrock import BedrockDecisionPolicy
from .models.mock_policy import MockDecisionPolicy
from .models.policy import DecisionPolicy
from .nodes.approval import human_approval_node
from .nodes.decision import make_agent_decision_node
from .nodes.observation import observation_node
from .nodes.perception import perception_node
from .nodes.response import make_final_response_node
from .nodes.tool_execution import make_tool_execution_node
from .observability.telemetry import traced_node
from .state import AgentState
from .tools.registry import ToolRegistry, build_default_registry

RouteTarget = Literal["tool_execution", "human_approval", "final_response"]


def build_decision_policy(config: AgentConfig, registry: ToolRegistry) -> DecisionPolicy:
    if config.mock_mode:
        return MockDecisionPolicy(registry)
    return BedrockDecisionPolicy(registry, config)


def make_router(registry: ToolRegistry, config: AgentConfig):
    def route(state: AgentState) -> RouteTarget:
        if state.get("iteration_count", 0) > config.max_iterations:
            return "final_response"

        action = state.get("current_action") or {}
        action_type = action.get("type")

        if action_type == "tool":
            spec = registry.get(action.get("tool"))
            if spec is None:
                return "final_response"
            already_cleared = state.get("approved") or config.auto_approve_high_risk
            if spec.risk == "high" and not already_cleared:
                return "human_approval"
            return "tool_execution"

        # complete, escalate, ask_customer -> all resolve through final_response
        return "final_response"

    return route


def build_graph(
    config: AgentConfig | None = None, policy: DecisionPolicy | None = None
) -> CompiledStateGraph:
    """`policy` is an injection seam for tests (e.g. a policy that never
    completes, to exercise the iteration guard) -- production code always
    lets `config.mock_mode` select the policy."""
    config = config or AgentConfig()
    registry = build_default_registry()
    policy = policy or build_decision_policy(config, registry)

    graph = StateGraph(AgentState)
    graph.add_node("perception", traced_node("perception", perception_node))
    graph.add_node(
        "agent_decision", traced_node("agent_decision", make_agent_decision_node(policy, registry))
    )
    # tool_execution manages its own per-tool span (tool.<name>) internally
    graph.add_node("tool_execution", make_tool_execution_node(registry))
    graph.add_node("observation", traced_node("observation", observation_node))
    graph.add_node("human_approval", traced_node("human_approval", human_approval_node))
    graph.add_node(
        "final_response", traced_node("final_response", make_final_response_node(registry, config))
    )

    graph.add_edge(START, "perception")
    graph.add_edge("perception", "agent_decision")
    graph.add_conditional_edges(
        "agent_decision",
        make_router(registry, config),
        {
            "tool_execution": "tool_execution",
            "human_approval": "human_approval",
            "final_response": "final_response",
        },
    )
    graph.add_edge("tool_execution", "observation")
    graph.add_edge("observation", "agent_decision")
    graph.add_edge("human_approval", "agent_decision")
    graph.add_edge("final_response", END)

    return graph.compile(checkpointer=MemorySaver())
