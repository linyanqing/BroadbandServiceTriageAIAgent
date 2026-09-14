"""Shared interface both orchestrator (supervisor-level) policies implement.

Mirrors models/policy.py's DecisionPolicy, one level up: where a
DecisionPolicy picks a tool (or finishes) within one specialist, an
OrchestratorPolicy picks a specialist to dispatch to (or finishes the whole
investigation).
"""

from __future__ import annotations

from typing import Protocol

from ..state import AgentState, OrchestratorAction


class OrchestratorPolicy(Protocol):
    def decide(self, state: AgentState) -> OrchestratorAction: ...
