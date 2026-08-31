"""AI-specific tracing/evaluation metadata for LangSmith.

LangSmith is complementary to, and distinct from, OpenTelemetry
(observability/telemetry.py): LangSmith captures agent runs, node/LLM/tool
calls, latency and token usage for AI evaluation; OpenTelemetry is the
vendor-neutral enterprise pipeline to CloudWatch/Cribl/Datadog/Splunk.
Neither replaces the other -- see docs/observability.md.

Tracing itself is controlled by the standard `LANGCHAIN_TRACING_V2` /
`LANGCHAIN_API_KEY` / `LANGCHAIN_PROJECT` env vars, which LangChain/LangSmith
read directly; this module only builds the redacted tags/metadata attached
to each run so raw customer PII never reaches LangSmith.
"""

from __future__ import annotations

from ..config import AgentConfig
from ..security.redaction import redact_dict
from ..state import AgentState


def build_run_config(state: AgentState, config: AgentConfig) -> dict:
    metadata = redact_dict(
        {
            "customer_id": state.get("customer_id"),
            "request_id": state.get("request_id"),
            "trace_id": state.get("trace_id"),
            "issue_type": state.get("issue_type"),
        }
    )
    return {
        "run_name": "broadband_triage",
        "tags": [
            f"environment:{config.environment}",
            "application:broadband-triage-agent",
            "use_case:customer_service",
            f"agent_version:{config.agent_version}",
        ],
        "metadata": metadata,
    }
