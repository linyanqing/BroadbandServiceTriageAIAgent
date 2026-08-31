"""Tool authorization boundary.

This is called from nodes/tool_execution.py for every tool call, regardless
of whether the decision came from Bedrock or the mock policy. It is the
actual enforcement point for "the LLM must not invent tools" -- a model that
requests an unregistered name gets an error observation back, never access.
"""

from __future__ import annotations

from ..tools.registry import ToolRegistry, ToolSpec


class ToolAuthorizationError(Exception):
    pass


def authorize_tool_call(registry: ToolRegistry, tool_name: str | None) -> ToolSpec:
    if not tool_name:
        raise ToolAuthorizationError("No tool name was provided")
    spec = registry.get(tool_name)
    if spec is None:
        raise ToolAuthorizationError(f"'{tool_name}' is not a registered enterprise tool")
    return spec
