"""Shared interface both decision policies implement."""

from __future__ import annotations

from typing import Protocol

from ..state import AgentState, CurrentAction


class DecisionPolicy(Protocol):
    def decide(self, state: AgentState) -> CurrentAction: ...


def latest_error(state: AgentState) -> str | None:
    for result in reversed(state.get("tool_results", [])):
        if result.get("error"):
            return result["error"]
    return None


def successful_results_by_tool(state: AgentState) -> dict[str, dict]:
    by_tool: dict[str, dict] = {}
    for result in state.get("tool_results", []):
        if not result.get("error"):
            by_tool[result["tool"]] = result
    return by_tool
