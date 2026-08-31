"""Deterministic stand-in for the Bedrock decision policy.

Used whenever MOCK_MODE=true (the default), so the agent runs and its tests
pass without AWS credentials or Bedrock model access. This is NOT a fixed
call sequence: every branch below reads only what is already in
state["tool_results"] and reacts to it, exactly like the real
BedrockDecisionPolicy is expected to given the same observations -- it is a
rule-based simulation of the same ReAct policy described in
prompts/system_prompt.py, not a scripted diagnostic path.
"""

from __future__ import annotations

from ..state import AgentState, CurrentAction
from ..tools.registry import ToolRegistry
from .policy import latest_error, successful_results_by_tool


class MockDecisionPolicy:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def decide(self, state: AgentState) -> CurrentAction:
        if state.get("approval_rejected"):
            return {
                "type": "escalate",
                "resolution": "escalated_error",
                "message": "The high-risk action was not approved by a human reviewer.",
            }

        error = latest_error(state)
        if error:
            return {"type": "escalate", "resolution": "escalated_error", "message": error}

        results = successful_results_by_tool(state)

        if "get_customer" not in results:
            return {
                "type": "tool",
                "tool": "get_customer",
                "tool_input": {"customer_id": state["customer_id"]},
                "reasoning": "Need the customer profile before investigating the service.",
            }

        if "get_broadband_service" not in results:
            return {
                "type": "tool",
                "tool": "get_broadband_service",
                "tool_input": {"customer_id": state["customer_id"]},
                "reasoning": "Need to identify the broadband service to diagnose.",
            }

        service_id = results["get_broadband_service"]["output"]["service_id"]

        if "check_outage" not in results:
            return {
                "type": "tool",
                "tool": "check_outage",
                "tool_input": {"service_id": service_id},
                "reasoning": "Check for a known outage before running diagnostics.",
            }

        outage = results["check_outage"]["output"]
        if outage.get("outage"):
            return {
                "type": "complete",
                "resolution": "known_outage",
                "diagnostic_summary": f"Known outage confirmed in {outage.get('region')}.",
            }

        if "run_network_diagnostics" not in results:
            return {
                "type": "tool",
                "tool": "run_network_diagnostics",
                "tool_input": {"service_id": service_id},
                "reasoning": "No outage found; run diagnostics on the line.",
            }

        diagnostics = results["run_network_diagnostics"]["output"]
        is_faulty = diagnostics.get("diagnosis") != "no_fault_detected"

        if is_faulty:
            if "create_fault_ticket" not in results:
                summary = (
                    f"diagnosis={diagnostics.get('diagnosis')}, "
                    f"packet_loss={diagnostics.get('packet_loss')}%, "
                    f"latency={diagnostics.get('latency_ms')}ms, "
                    f"error_rate={diagnostics.get('error_rate')}%"
                )
                return {
                    "type": "tool",
                    "tool": "create_fault_ticket",
                    "tool_input": {"service_id": service_id, "diagnostic_summary": summary},
                    "reasoning": "Diagnostics indicate a fault; raise a ticket.",
                }
            return {
                "type": "complete",
                "resolution": "fault_ticket_created",
                "diagnostic_summary": "Fault ticket created after diagnostics indicated a line fault.",
            }

        return {
            "type": "complete",
            "resolution": "healthy_no_action",
            "diagnostic_summary": "Diagnostics show no fault; provided troubleshooting guidance.",
        }
