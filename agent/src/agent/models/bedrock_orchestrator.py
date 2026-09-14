"""Bedrock-backed supervisor/orchestrator policy.

Mirrors models/bedrock.py's BedrockDecisionPolicy one level up: the model is
offered a synthetic `dispatch` tool (naming exactly the four specialists)
plus a `finish` control action, using the same redacted-context-JSON and
defensive-parsing patterns. It never sees or names a specialist's own tools
-- only `graph.py` wires a specialist's scoped registry into that
specialist's own BedrockDecisionPolicy once dispatched.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_aws import ChatBedrockConverse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..config import AgentConfig
from ..prompts.system_prompt import SUPERVISOR_PROMPT
from ..security.redaction import redact_dict
from ..state import AgentState, OrchestratorAction, SpecialistName

_SPECIALIST_NAMES: list[SpecialistName] = [
    "network_diagnose",
    "billing",
    "line_testing",
    "equipment_reset",
]

_DISPATCH_TOOL = {
    "type": "function",
    "function": {
        "name": "dispatch",
        "description": "Dispatch the investigation to one specialist agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "specialist": {"type": "string", "enum": _SPECIALIST_NAMES},
                "reasoning": {"type": "string"},
            },
            "required": ["specialist", "reasoning"],
        },
    },
}

_FINISH_TOOL = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": "Call this once every specialist needed has run and the investigation is complete.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def build_orchestrator_messages(state: AgentState) -> list:
    """Redacted, structured summary of state -- mirrors
    models/bedrock.py's build_decision_messages, one level up: specialist
    results rather than raw tool observations."""

    context = {
        "customer_id": state.get("customer_id"),
        "issue_type": state.get("issue_type"),
        "confidence": state.get("confidence"),
        "billing_flagged": state.get("billing_flagged", False),
        "specialist_history": state.get("specialist_history", []),
        "specialist_results": [redact_dict(dict(r)) for r in state.get("specialist_results", [])],
    }
    return [
        SystemMessage(content=SUPERVISOR_PROMPT),
        HumanMessage(content=json.dumps(context, default=str)),
    ]


def _action_from_tool_call(call: dict[str, Any]) -> OrchestratorAction:
    name = call.get("name")
    args = call.get("args", {}) or {}

    if name == "finish":
        return {"type": "complete"}

    if name == "dispatch":
        specialist = args.get("specialist")
        if specialist not in _SPECIALIST_NAMES:
            return {
                "type": "escalate",
                "resolution": "escalated_invalid_action",
                "message": f"Model attempted to dispatch to unknown specialist '{specialist}'.",
            }
        return {"type": "dispatch", "specialist": specialist, "reasoning": args.get("reasoning")}

    return {
        "type": "escalate",
        "resolution": "escalated_invalid_action",
        "message": f"Model attempted to call unregistered action '{name}'.",
    }


def parse_orchestrator_decision(response: AIMessage) -> OrchestratorAction:
    tool_calls = getattr(response, "tool_calls", None) or []
    if not tool_calls:
        return {
            "type": "escalate",
            "resolution": "escalated_error",
            "message": "Model response did not include a tool call.",
        }
    return _action_from_tool_call(tool_calls[0])


class BedrockOrchestratorPolicy:
    def __init__(self, config: AgentConfig) -> None:
        self.llm = ChatBedrockConverse(
            model=config.bedrock_model_id,
            region_name=config.aws_region,
        ).bind_tools([_DISPATCH_TOOL, _FINISH_TOOL])

    def decide(self, state: AgentState) -> OrchestratorAction:
        messages = build_orchestrator_messages(state)
        response = self.llm.invoke(messages)
        return parse_orchestrator_decision(response)
