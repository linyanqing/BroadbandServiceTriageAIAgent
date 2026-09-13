"""Bedrock-backed decision policy.

Used whenever MOCK_MODE=false. This one class is reused, unmodified, for
every specialist (network_diagnose, billing, line_testing, equipment_reset)
-- what scopes each instance is simply which `registry` and `resolutions`
it's constructed with. The model is only ever offered that registry's tool
specs plus a `finish` control action limited to that specialist's own valid
resolutions -- it cannot see or name anything else. `_action_from_tool_call`
(the parsing logic) is pure and unit-tested independently of any live
Bedrock call.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_aws import ChatBedrockConverse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..config import AgentConfig
from ..prompts.system_prompt import SYSTEM_PROMPT
from ..security.redaction import redact_dict
from ..state import AgentState, CurrentAction
from ..tools.registry import ToolRegistry
from .policy import latest_error

# network_diagnose's resolutions -- the default for any BedrockDecisionPolicy
# that doesn't pass its own `resolutions` (keeps existing behavior/tests
# unchanged for that specialist).
_DEFAULT_RESOLUTIONS = [
    "known_outage",
    "fault_ticket_created",
    "healthy_no_action",
    "escalated_error",
]


def build_finish_tool(resolutions: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "finish",
            "description": (
                "Call this once you have enough evidence to conclude the investigation. "
                "resolution and diagnostic_summary must be grounded only in tool observations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "resolution": {"type": "string", "enum": resolutions},
                    "diagnostic_summary": {"type": "string"},
                },
                "required": ["resolution", "diagnostic_summary"],
            },
        },
    }


def _tool_dict(spec) -> dict:
    schema = spec.input_schema.model_json_schema()
    schema.pop("title", None)
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": schema,
        },
    }


def build_tool_specs_for_llm(
    registry: ToolRegistry, resolutions: list[str] | None = None
) -> list[dict]:
    return [_tool_dict(spec) for spec in registry.all()] + [
        build_finish_tool(resolutions or _DEFAULT_RESOLUTIONS)
    ]


def build_decision_messages(state: AgentState, system_prompt: str) -> list:
    """Redacted, structured summary of state -- never the raw customer_message
    verbatim beyond what perception already classified, keeping PII exposure
    to the model itself minimal and auditable."""

    error = latest_error(state)
    context = {
        # Not PII -- an internal correlation identifier the model needs to
        # call get_customer/get_broadband_service. See security/redaction.py.
        "customer_id": state.get("customer_id"),
        "issue_type": state.get("issue_type"),
        "confidence": state.get("confidence"),
        "service_id": state.get("service_id"),
        "observations": [redact_dict(o) for o in state.get("observations", [])],
        "last_error": error,
        "approval_rejected": state.get("approval_rejected", False),
    }
    return [
        SystemMessage(content=system_prompt),
        HumanMessage(content=json.dumps(context, default=str)),
    ]


def _action_from_tool_call(call: dict[str, Any], registry: ToolRegistry) -> CurrentAction:
    name = call.get("name")
    args = call.get("args", {}) or {}

    if name == "finish":
        return {
            "type": "complete",
            "resolution": args.get("resolution", "escalated_error"),
            "diagnostic_summary": args.get("diagnostic_summary", ""),
        }

    if registry.get(name) is None:
        return {
            "type": "escalate",
            "resolution": "escalated_invalid_action",
            "message": f"Model attempted to call unregistered tool '{name}'.",
        }

    return {"type": "tool", "tool": name, "tool_input": args}


def parse_decision(response: AIMessage, registry: ToolRegistry) -> CurrentAction:
    tool_calls = getattr(response, "tool_calls", None) or []
    if not tool_calls:
        return {
            "type": "escalate",
            "resolution": "escalated_error",
            "message": "Model response did not include a tool call.",
        }
    return _action_from_tool_call(tool_calls[0], registry)


class BedrockDecisionPolicy:
    def __init__(
        self,
        registry: ToolRegistry,
        config: AgentConfig,
        resolutions: list[str] | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self._registry = registry
        self._system_prompt = system_prompt or SYSTEM_PROMPT
        tool_specs = build_tool_specs_for_llm(registry, resolutions)
        self.llm = ChatBedrockConverse(
            model=config.bedrock_model_id,
            region_name=config.aws_region,
        ).bind_tools(tool_specs)

    def decide(self, state: AgentState) -> CurrentAction:
        if state.get("approval_rejected"):
            return {
                "type": "escalate",
                "resolution": "escalated_error",
                "message": "The high-risk action was not approved by a human reviewer.",
            }
        messages = build_decision_messages(state, self._system_prompt)
        response = self.llm.invoke(messages)
        return parse_decision(response, self._registry)
