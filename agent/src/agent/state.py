"""Explicit LangGraph state for the broadband triage agent.

Two decision levels share this one state object: the top-level supervisor
(orchestrator_action, specialist_history/results) and whichever specialist
subgraph is currently running (current_action, tool_calls/results,
observations -- specialist-local in spirit but kept in shared state so a
later specialist can see an earlier one's observations for free, and so
nodes/response.py's tool_results lookups keep working unmodified). See
docs/agent-design.md for the full rationale.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict


def dedup_add(existing: list, new: list) -> list:
    """Reducer for every Annotated list field that accumulates across a
    whole investigation. Unlike plain operator.add, an entry already
    present (by value equality) is not appended again -- needed because a
    confirmed LangGraph replay behavior (see docs/agent-design.md's
    "Interrupt replay and idempotency") can re-invoke an upstream node a
    second time within one graph.invoke() call, and without this, that
    replay would silently grow these lists with duplicate entries.

    This is safe specifically because every list it's used for cannot have
    a *legitimate* duplicate: tool_execution's own _already_executed guard
    already makes a replayed tool call return byte-identical output, so a
    repeated tool_calls/tool_results/observations entry really is a replay
    artifact, never a second distinct result; and the supervisor's router
    structurally refuses to dispatch the same specialist twice, so a
    repeated specialist_history/specialist_results entry is unambiguously
    a replay artifact too. This is also what keeps
    models/decision_cache.py's cache keys stable across a replay -- without
    it, a replayed upstream node would grow these lists before the
    downstream decision node re-runs, changing its cache key and defeating
    that cache.
    """
    result = list(existing)
    for item in new:
        if item not in result:
            result.append(item)
    return result


ActionType = Literal["tool", "ask_customer", "human_approval", "complete", "escalate"]

SpecialistName = Literal["network_diagnose", "billing", "line_testing", "equipment_reset"]
OrchestratorActionType = Literal["dispatch", "complete", "escalate"]

Resolution = Literal[
    "known_outage",
    "fault_ticket_created",
    "healthy_no_action",
    "billing_hold_resolved",
    "billing_no_issue_found",
    "line_test_fault_confirmed",
    "line_test_passed",
    "equipment_reset_completed",
    "equipment_reset_not_required",
    "escalated_error",
    "escalated_invalid_action",
    "escalated_timeout",
]
Status = Literal["in_progress", "resolved", "escalated", "awaiting_approval"]


class ToolCallRecord(TypedDict):
    tool: str
    input: dict[str, Any]


class ToolResultRecord(TypedDict):
    tool: str
    input: dict[str, Any]
    output: dict[str, Any]
    error: str | None


class Observation(TypedDict):
    tool: str
    result: dict[str, Any]


class CurrentAction(TypedDict, total=False):
    """A specialist's own in-progress decision -- never read at the top
    level anymore (that's OrchestratorAction's job)."""

    type: ActionType
    tool: str | None
    tool_input: dict[str, Any] | None
    reasoning: str | None
    resolution: Resolution | None
    diagnostic_summary: str | None
    message: str | None


class OrchestratorAction(TypedDict, total=False):
    """The supervisor's own in-progress decision: dispatch a specialist, or
    finish the whole investigation."""

    type: OrchestratorActionType
    specialist: SpecialistName | None
    reasoning: str | None
    resolution: Resolution | None
    diagnostic_summary: str | None
    message: str | None


class SpecialistResult(TypedDict):
    """What a specialist subgraph hands back to the supervisor on exit."""

    specialist: SpecialistName
    resolution: Resolution
    diagnostic_summary: str | None


class AgentState(TypedDict, total=False):
    # perception
    customer_message: str
    customer_id: str
    request_id: str
    trace_id: str
    intent: str | None
    issue_type: str | None
    confidence: float | None
    billing_flagged: bool | None

    # working context, shared across context_gathering + every specialist
    service_id: str | None
    observations: Annotated[list[Observation], dedup_add]
    tool_calls: Annotated[list[ToolCallRecord], dedup_add]
    tool_results: Annotated[list[ToolResultRecord], dedup_add]

    # specialist-local decision / control (reset between specialist dispatches)
    current_action: CurrentAction | None
    requires_human_approval: bool
    approved: bool | None
    approval_rejected: bool | None
    iteration_count: int

    # Cache of specialist agent_decision outputs, keyed by a hash of the
    # input state a decision reads (models/decision_cache.py) -- guards a
    # confirmed LangGraph replay behavior from re-invoking a (possibly
    # live-LLM-backed) policy for a state it already decided on. Merged via
    # dict union (operator.or_), never overwritten wholesale, since two
    # different specialists' decisions accumulate over one investigation.
    decision_cache: Annotated[dict[str, CurrentAction], operator.or_]

    # supervisor-level decision / control
    orchestrator_action: OrchestratorAction | None
    specialist_history: Annotated[list[SpecialistName], dedup_add]
    specialist_results: Annotated[list[SpecialistResult], dedup_add]
    specialist_dispatch_count: int
    # Same idea as decision_cache, one level up, for the supervisor's own
    # dispatch/finish decision.
    orchestrator_decision_cache: Annotated[dict[str, OrchestratorAction], operator.or_]

    # outcome
    status: Status
    diagnostic_summary: str | None
    resolution: Resolution | None
    ticket_id: str | None
    final_response: str | None
