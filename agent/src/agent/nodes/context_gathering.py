"""Node: context_gathering -- unconditionally fetches the customer profile
then the broadband service before any specialist is ever dispatched.

Deliberately a single plain node, not a ReAct subgraph: this sequence is
fixed (get_customer, then get_broadband_service) with no possible
high-risk/approval branch and no policy decision needed even in principle,
so a full specialist subgraph was unnecessary machinery for it.

Even as a plain node, this (and any other plain node preceding a specialist
dispatch) is still empirically subject to a confirmed LangGraph behavior:
once a specialist *subgraph* actually executes within a given
graph.invoke() call -- interrupted or not, it doesn't matter -- whatever
plain nodes ran earlier in that same call get re-invoked for real. See
docs/agent-design.md's "Interrupt replay and idempotency" section for the
full writeup and why tool_execution's _already_executed guard (which this
node calls into directly, via make_tool_execution_node) is what actually
keeps that safe rather than merely cosmetic.
"""

from __future__ import annotations

from collections.abc import Callable

from ..state import AgentState
from ..tools.registry import ToolRegistry
from .observation import observation_node
from .tool_execution import make_tool_execution_node


def make_context_gathering_node(registry: ToolRegistry) -> Callable:
    execute_tool = make_tool_execution_node(registry)

    def node(state: AgentState) -> dict:
        current = dict(state)
        new_tool_calls: list = []
        new_tool_results: list = []
        new_observations: list = []
        service_id = current.get("service_id")

        for tool_name in ("get_customer", "get_broadband_service"):
            current["current_action"] = {
                "type": "tool",
                "tool": tool_name,
                "tool_input": {"customer_id": current["customer_id"]},
            }

            exec_update = execute_tool(current)
            new_tool_calls.extend(exec_update.get("tool_calls", []))
            new_tool_results.extend(exec_update.get("tool_results", []))
            current["tool_calls"] = current.get("tool_calls", []) + exec_update.get(
                "tool_calls", []
            )
            current["tool_results"] = current.get("tool_results", []) + exec_update.get(
                "tool_results", []
            )

            obs_update = observation_node(current)
            new_observations.extend(obs_update.get("observations", []))
            current["observations"] = current.get("observations", []) + obs_update.get(
                "observations", []
            )
            if "service_id" in obs_update:
                service_id = obs_update["service_id"]
                current["service_id"] = service_id

        return {
            "tool_calls": new_tool_calls,
            "tool_results": new_tool_results,
            "observations": new_observations,
            "service_id": service_id,
        }

    return node
