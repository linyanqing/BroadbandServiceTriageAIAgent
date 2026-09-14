"""Node 3: tool_execution -- the only place a tool actually runs.

Validates the tool name against the registry (security/authorization.py) and
the input against the tool's pydantic schema before calling the deterministic
handler. The LLM never touches this code path directly.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import ValidationError

from ..observability.telemetry import traced_step
from ..security.authorization import ToolAuthorizationError, authorize_tool_call
from ..state import AgentState, ToolResultRecord
from ..tools.registry import ToolRegistry


def _already_executed(state: AgentState, tool_name: str, tool_input: dict) -> dict | None:
    """Guards against a tool's handler running twice for the exact same
    call. Needed because of an empirically-confirmed LangGraph replay
    behavior in this multi-subgraph design: once ANY specialist subgraph
    actually executes within a graph.invoke() call -- whether or not a
    human_approval interrupt is involved -- whatever plain nodes or
    already-completed specialist subgraphs ran earlier in that same call
    get re-invoked for real (see docs/agent-design.md's "Interrupt replay
    and idempotency"). tool_results accumulates durably across that replay
    even though the node execution itself re-runs, so this state-based
    check is what actually stops a real backend (billing, equipment reset,
    ...) from being called twice -- the mock handlers here happen to be
    pure functions, but a live integration would not be."""
    for result in state.get("tool_results", []):
        if (
            result["tool"] == tool_name
            and result.get("input") == tool_input
            and not result.get("error")
        ):
            return result["output"]
    return None


def make_tool_execution_node(registry: ToolRegistry) -> Callable:
    def node(state: AgentState) -> dict:
        action = state.get("current_action") or {}
        tool_name = action.get("tool")
        tool_input = action.get("tool_input") or {}

        with traced_step(f"tool.{tool_name or 'unknown'}", tool=tool_name):
            try:
                spec = authorize_tool_call(registry, tool_name)
                validated_input = spec.input_schema(**tool_input)
                cached_output = _already_executed(state, tool_name, tool_input)
                output = (
                    cached_output if cached_output is not None else spec.handler(validated_input)
                )
                result: ToolResultRecord = {
                    "tool": tool_name,
                    "input": tool_input,
                    "output": output,
                    "error": None,
                }
            except (ToolAuthorizationError, ValidationError) as exc:
                result = {
                    "tool": tool_name or "unknown",
                    "input": tool_input,
                    "output": {},
                    "error": str(exc),
                }

        return {
            "tool_calls": [{"tool": tool_name, "input": tool_input}],
            "tool_results": [result],
            # a high-risk approval is single-use
            "approved": None,
        }

    return node
