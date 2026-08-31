"""Explicit LangGraph state for the broadband triage agent."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

ActionType = Literal["tool", "ask_customer", "human_approval", "complete", "escalate"]
Resolution = Literal[
    "known_outage",
    "fault_ticket_created",
    "healthy_no_action",
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
    type: ActionType
    tool: str | None
    tool_input: dict[str, Any] | None
    reasoning: str | None
    resolution: Resolution | None
    diagnostic_summary: str | None
    message: str | None


class AgentState(TypedDict, total=False):
    # perception
    customer_message: str
    customer_id: str
    request_id: str
    trace_id: str
    intent: str | None
    issue_type: str | None
    confidence: float | None

    # working context, accumulated across the ReAct loop
    service_id: str | None
    observations: Annotated[list[Observation], operator.add]
    tool_calls: Annotated[list[ToolCallRecord], operator.add]
    tool_results: Annotated[list[ToolResultRecord], operator.add]

    # decision / control
    current_action: CurrentAction | None
    requires_human_approval: bool
    approved: bool | None
    approval_rejected: bool | None
    iteration_count: int

    # outcome
    status: Status
    diagnostic_summary: str | None
    resolution: Resolution | None
    ticket_id: str | None
    final_response: str | None
