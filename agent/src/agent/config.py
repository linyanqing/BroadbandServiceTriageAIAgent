"""Runtime configuration, sourced from environment variables / .env.

Nothing here hard-codes a Bedrock model ID, a Datadog/Splunk endpoint, or any
secret. Enterprise telemetry destinations (Cribl/Datadog/Splunk) are reached
indirectly through the ADOT collector endpoint below -- this application only
ever talks OTLP to that collector, never to Datadog/Splunk directly.
"""

from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    environment: str = Field(default="local")
    agent_version: str = Field(default="0.1.0")

    # MOCK_MODE governs Bedrock + AI-observability mocking only. The five
    # enterprise tools (customer/service/outage/diagnostics/fault) are always
    # deterministic in-process mocks in this POC -- see tools/.
    mock_mode: bool = Field(default=True)

    bedrock_model_id: str | None = Field(default=None)
    aws_region: str = Field(default="ap-southeast-2")

    # Whether a high-risk tool (create_fault_ticket) can proceed without a
    # human_approval interrupt. Defaults to False so the POC demonstrates the
    # pause/resume boundary by default (scenario D).
    auto_approve_high_risk: bool = Field(default=False)

    max_iterations: int = Field(default=8)
    # Ceiling on how many times the supervisor may be invoked in one
    # investigation -- the supervisor-level analogue of max_iterations.
    # This counts every supervisor call, dispatch or finish alike: 4
    # specialists exist and the router refuses to dispatch the same one
    # twice, so the absolute worst case is dispatch, dispatch, dispatch,
    # dispatch, then a final complete/escalate decision -- 5 calls -- plus
    # one spare for a genuine edge case. (A confirmed LangGraph replay
    # behavior can re-invoke this node -- see docs/agent-design.md's
    # "Interrupt replay and idempotency" -- but state.py's dedup_add
    # reducer keeps specialist_history/specialist_results byte-identical
    # across that replay, so it no longer inflates this counter; no extra
    # headroom needed for it.)
    max_specialist_dispatches: int = Field(default=6)

    langsmith_enabled: bool = Field(default=False, validation_alias="LANGCHAIN_TRACING_V2")
    langsmith_project: str = Field(
        default="broadband-triage-agent", validation_alias="LANGCHAIN_PROJECT"
    )

    otel_enabled: bool = Field(default=False)
    otel_exporter_endpoint: str = Field(
        default="http://localhost:4318", validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )

    @model_validator(mode="after")
    def _require_model_id_outside_mock_mode(self) -> AgentConfig:
        if not self.mock_mode and not self.bedrock_model_id:
            raise ValueError(
                "BEDROCK_MODEL_ID must be set when MOCK_MODE=false "
                "(no hard-coded default model is permitted)"
            )
        return self


def load_config() -> AgentConfig:
    return AgentConfig()
