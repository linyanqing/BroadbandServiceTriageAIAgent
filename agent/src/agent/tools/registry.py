"""Explicit allow-list of deterministic enterprise tools.

This is the single source of truth for what the LLM is permitted to invoke.
`tool_execution` (nodes/tool_execution.py) validates every call against this
registry regardless of what the model asked for -- the registry, not the
prompt, is what actually prevents the LLM from inventing or accessing tools
outside this list.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
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
    # Optional per-call override of `risk`, given the raw (unvalidated)
    # tool_input dict -- e.g. a billing credit under some dollar threshold
    # can auto-approve while a larger one requires a human. None (the
    # default, and every tool but apply_billing_credit) means `risk` is
    # used as-is; see effective_risk() below, which both call sites
    # (nodes/decision.py, graph.py's router) use instead of reading
    # `.risk` directly.
    risk_override: Callable[[dict], RiskLevel] | None = None


def effective_risk(spec: ToolSpec, tool_input: dict) -> RiskLevel:
    return spec.risk_override(tool_input) if spec.risk_override else spec.risk


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

    def subset(self, names: Iterable[str]) -> ToolRegistry:
        """A registry scoped to only the given tool names, referencing the
        same ToolSpec objects. This -- not any new logic in
        security/authorization.py -- is what actually stops a specialist
        agent from reaching another specialist's tools: authorize_tool_call
        just checks registry membership, so a scoped registry that doesn't
        contain a name makes it unreachable regardless of what any policy
        (mock or Bedrock) asks for."""
        scoped = ToolRegistry()
        for name in names:
            spec = self.get(name)
            if spec is None:
                raise ValueError(f"Cannot scope unregistered tool '{name}'")
            scoped._tools[name] = spec
        return scoped


def build_default_registry() -> ToolRegistry:
    from .billing import register_billing_tools
    from .customer import register_customer_tool
    from .diagnostics import register_diagnostics_tool
    from .equipment_reset import register_equipment_reset_tools
    from .fault import register_fault_tool
    from .line_testing import register_line_testing_tools
    from .outage import register_outage_tool
    from .service import register_service_tool

    registry = ToolRegistry()
    for register in (
        register_customer_tool,
        register_service_tool,
        register_outage_tool,
        register_diagnostics_tool,
        register_fault_tool,
        register_billing_tools,
        register_line_testing_tools,
        register_equipment_reset_tools,
    ):
        register(registry)
    return registry


# Which tools each specialist agent is scoped to -- see ToolRegistry.subset().
# "core" is context_gathering's scope, run before any specialist is picked.
SPECIALIST_TOOL_SCOPES: dict[str, tuple[str, ...]] = {
    "core": ("get_customer", "get_broadband_service"),
    "network_diagnose": ("check_outage", "run_network_diagnostics", "create_fault_ticket"),
    "billing": ("check_billing_status", "apply_billing_credit"),
    "line_testing": ("run_remote_line_test", "schedule_technician_visit"),
    "equipment_reset": ("run_equipment_diagnostics", "trigger_equipment_reset"),
}
