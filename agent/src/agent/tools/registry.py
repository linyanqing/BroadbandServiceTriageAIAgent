"""Explicit allow-list of deterministic enterprise tools.

This is the single source of truth for what the LLM is permitted to invoke.
`tool_execution` (nodes/tool_execution.py) validates every call against this
registry regardless of what the model asked for -- the registry, not the
prompt, is what actually prevents the LLM from inventing or accessing tools
outside this list.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

RiskLevel = Literal["low", "high"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: type[BaseModel]
    risk: RiskLevel
    handler: Callable[[BaseModel], dict]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool '{spec.name}' is already registered")
        self._tools[spec.name] = spec

    def get(self, name: str | None) -> ToolSpec | None:
        if not name:
            return None
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())


def build_default_registry() -> ToolRegistry:
    from .customer import register_customer_tool
    from .diagnostics import register_diagnostics_tool
    from .fault import register_fault_tool
    from .outage import register_outage_tool
    from .service import register_service_tool

    registry = ToolRegistry()
    for register in (
        register_customer_tool,
        register_service_tool,
        register_outage_tool,
        register_diagnostics_tool,
        register_fault_tool,
    ):
        register(registry)
    return registry
