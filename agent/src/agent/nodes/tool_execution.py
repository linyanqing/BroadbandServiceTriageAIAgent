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


def make_tool_execution_node(registry: ToolRegistry) -> Callable:
    def node(state: AgentState) -> dict:
        action = state.get("current_action") or {}
        tool_name = action.get("tool")
        tool_input = action.get("tool_input") or {}

        with traced_step(f"tool.{tool_name or 'unknown'}", tool=tool_name):
            try:
                spec = authorize_tool_call(registry, tool_name)
                validated_input = spec.input_schema(**tool_input)
                output = spec.handler(validated_input)
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
