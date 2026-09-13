"""Builds the bounded LangGraph multi-agent triage system.

Topology: perception -> context_gathering -> supervisor -> (one specialist
subgraph, chosen per turn) -> supervisor (loop) -> ... -> final_response.

The graph fixes the allowed structure (which specialists exist, which tools
each is scoped to, where the human-approval boundary sits within each);
the orchestrator policy plugged into `supervisor` is what actually chooses
which specialist to dispatch at runtime, and each specialist's own decision
policy is what chooses its next tool call -- see docs/agent-design.md for
the full rationale, and specialist_graph.py for why every specialist shares
one generic subgraph shape rather than four bespoke ones.
"""

from __future__ import annotations

from typing import Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .config import AgentConfig
from .models.bedrock import BedrockDecisionPolicy
from .models.bedrock_orchestrator import BedrockOrchestratorPolicy
from .models.mock_orchestrator import MockOrchestratorPolicy
from .models.mock_policy import MockNetworkDiagnosePolicy
from .models.mock_specialists import (
    MockBillingPolicy,
    MockEquipmentResetPolicy,
    MockLineTestingPolicy,
)
from .models.orchestrator_policy import OrchestratorPolicy
from .models.policy import DecisionPolicy
from .nodes.context_gathering import make_context_gathering_node
from .nodes.perception import perception_node
from .nodes.response import make_final_response_node
from .nodes.supervisor import make_supervisor_node
from .observability.telemetry import traced_node
from .prompts.system_prompt import (
    BILLING_PROMPT,
    EQUIPMENT_RESET_PROMPT,
    LINE_TESTING_PROMPT,
    SYSTEM_PROMPT,
)
from .specialist_graph import build_specialist_subgraph
from .state import AgentState, SpecialistName
from .tools.registry import SPECIALIST_TOOL_SCOPES, ToolRegistry, build_default_registry

SupervisorRouteTarget = Literal[
    "network_diagnose_agent",
    "billing_agent",
    "line_testing_agent",
    "equipment_reset_agent",
    "final_response",
]

_SPECIALIST_NAMES: tuple[SpecialistName, ...] = (
    "network_diagnose",
    "billing",
    "line_testing",
    "equipment_reset",
)

_SPECIALIST_EDGE_NAMES: dict[str, str] = {name: f"{name}_agent" for name in _SPECIALIST_NAMES}

# Per-specialist Bedrock configuration -- which resolutions its `finish`
# tool may offer, and which completion rules its system prompt states.
# Every specialist reuses the same BedrockDecisionPolicy class (models/bedrock.py)
# unmodified; only these two things (plus its scoped registry) differ.
_SPECIALIST_BEDROCK_CONFIG: dict[SpecialistName, dict] = {
    "network_diagnose": {
        "resolutions": [
            "known_outage",
            "fault_ticket_created",
            "healthy_no_action",
            "escalated_error",
        ],
        "system_prompt": SYSTEM_PROMPT,
    },
    "billing": {
        "resolutions": ["billing_hold_resolved", "billing_no_issue_found", "escalated_error"],
        "system_prompt": BILLING_PROMPT,
    },
    "line_testing": {
        "resolutions": ["line_test_fault_confirmed", "line_test_passed", "escalated_error"],
        "system_prompt": LINE_TESTING_PROMPT,
    },
    "equipment_reset": {
        "resolutions": [
            "equipment_reset_completed",
            "equipment_reset_not_required",
            "escalated_error",
        ],
        "system_prompt": EQUIPMENT_RESET_PROMPT,
    },
}


def build_specialist_policies(
    config: AgentConfig, scoped_registries: dict[SpecialistName, ToolRegistry]
) -> dict[SpecialistName, DecisionPolicy]:
    """lets `config.mock_mode` select mock vs. Bedrock for every specialist
    at once, mirroring the single-agent design's build_decision_policy."""
    if config.mock_mode:
        return {
            "network_diagnose": MockNetworkDiagnosePolicy(scoped_registries["network_diagnose"]),
            "billing": MockBillingPolicy(scoped_registries["billing"]),
            "line_testing": MockLineTestingPolicy(scoped_registries["line_testing"]),
            "equipment_reset": MockEquipmentResetPolicy(scoped_registries["equipment_reset"]),
        }
    return {
        name: BedrockDecisionPolicy(
            scoped_registries[name],
            config,
            resolutions=bedrock_config["resolutions"],
            system_prompt=bedrock_config["system_prompt"],
        )
        for name, bedrock_config in _SPECIALIST_BEDROCK_CONFIG.items()
    }


def build_orchestrator_policy(config: AgentConfig) -> OrchestratorPolicy:
    if config.mock_mode:
        return MockOrchestratorPolicy()
    return BedrockOrchestratorPolicy(config)


def make_supervisor_router(config: AgentConfig):
    def route(state: AgentState) -> SupervisorRouteTarget:
        if state.get("specialist_dispatch_count", 0) > config.max_specialist_dispatches:
            return "final_response"

        action = state.get("orchestrator_action") or {}
        if action.get("type") != "dispatch":
            return "final_response"

        specialist = action.get("specialist")
        edge = _SPECIALIST_EDGE_NAMES.get(specialist)
        if edge is None:
            return "final_response"
        if specialist in state.get("specialist_history", []):
            # Never dispatch the same specialist twice -- structurally
            # enforced here too, not just stated in SUPERVISOR_PROMPT.
            # final_response falls back to the last specialist's real
            # result in this case, same as a normal "complete".
            return "final_response"
        return edge

    return route


def build_graph(
    config: AgentConfig | None = None,
    orchestrator_policy: OrchestratorPolicy | None = None,
    specialist_policy_overrides: dict[SpecialistName, DecisionPolicy] | None = None,
) -> CompiledStateGraph:
    """`orchestrator_policy`/`specialist_policy_overrides` are injection seams
    for tests (e.g. a policy that never completes, to exercise the
    iteration/dispatch guards) -- production code always lets
    `config.mock_mode` select every policy via build_specialist_policies/
    build_orchestrator_policy."""
    config = config or AgentConfig()
    registry = build_default_registry()

    core_registry = registry.subset(SPECIALIST_TOOL_SCOPES["core"])
    scoped_registries: dict[SpecialistName, ToolRegistry] = {
        name: registry.subset(SPECIALIST_TOOL_SCOPES[name]) for name in _SPECIALIST_NAMES
    }

    specialist_policies = build_specialist_policies(config, scoped_registries)
    if specialist_policy_overrides:
        specialist_policies.update(specialist_policy_overrides)
    orchestrator_policy = orchestrator_policy or build_orchestrator_policy(config)

    graph = StateGraph(AgentState)
    graph.add_node("perception", traced_node("perception", perception_node))
    # Not traced_node-wrapped: its implementation internally calls
    # tool_execution, which already opens its own "tool.<name>" span per
    # call -- an OUTER "node.context_gathering" span wrapping that inner,
    # independently-spanned work was empirically confirmed to cause this
    # node's real work to execute twice (root cause not fully understood --
    # suspected OpenTelemetry span-context/contextvars interaction with
    # LangGraph's own task tracking -- but reproducibly tied to the nested
    # traced_step calls specifically). Every other traced_node usage in
    # this codebase wraps a node with no further nested traced_step calls
    # of its own, so this is the one place that pattern doesn't hold.
    graph.add_node("context_gathering", make_context_gathering_node(core_registry))
    graph.add_node(
        "supervisor", traced_node("supervisor", make_supervisor_node(orchestrator_policy))
    )
    for name in _SPECIALIST_NAMES:
        graph.add_node(
            f"{name}_agent",
            build_specialist_subgraph(
                name, specialist_policies[name], scoped_registries[name], config
            ),
        )
    graph.add_node(
        "final_response", traced_node("final_response", make_final_response_node(config))
    )

    graph.add_edge(START, "perception")
    graph.add_edge("perception", "context_gathering")
    graph.add_edge("context_gathering", "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        make_supervisor_router(config),
        {
            **{f"{name}_agent": f"{name}_agent" for name in _SPECIALIST_NAMES},
            "final_response": "final_response",
        },
    )
    for name in _SPECIALIST_NAMES:
        graph.add_edge(f"{name}_agent", "supervisor")
    graph.add_edge("final_response", END)

    return graph.compile(checkpointer=MemorySaver())
