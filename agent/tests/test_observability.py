from opentelemetry import metrics, trace

from agent.config import AgentConfig
from agent.observability import telemetry
from agent.observability.langsmith import build_run_config


def test_otel_disabled_is_a_safe_noop():
    telemetry.setup_telemetry(AgentConfig(otel_enabled=False))
    assert telemetry.is_configured() is False
    with telemetry.traced_step("test.span", foo="bar"):
        pass
    assert set(telemetry.get_meter_instruments().keys()) == {
        "request_count",
        "request_latency",
        "tool_latency",
        "error_count",
        "agent_execution_duration",
    }


def test_otel_enabled_configures_a_real_provider_without_network_access():
    # No collector is running on this port; construction must still succeed
    # (spans/metrics only attempt export on a background flush interval).
    telemetry.setup_telemetry(
        AgentConfig(otel_enabled=True, otel_exporter_endpoint="http://localhost:4318")
    )
    assert telemetry.is_configured() is True
    with telemetry.traced_step("test.span", foo="bar"):
        pass

    # Shut the exporters down immediately so their background retry threads
    # don't keep hammering the unreachable endpoint for the rest of the suite.
    trace.get_tracer_provider().shutdown()
    metrics.get_meter_provider().shutdown()
    telemetry.setup_telemetry(AgentConfig(otel_enabled=False))


def test_traced_step_records_error_metric_on_exception():
    telemetry.setup_telemetry(AgentConfig(otel_enabled=False))
    try:
        with telemetry.traced_step("test.failing_span"):
            raise ValueError("boom")
    except ValueError:
        pass


def test_langsmith_run_config_redacts_pii_and_sets_expected_tags():
    config = AgentConfig(environment="test", agent_version="9.9.9")
    state = {
        "customer_id": "123",
        "request_id": "REQ-1",
        "trace_id": "TRACE-1",
        "issue_type": "intermittent_connection",
        "customer_message": "My name is Jane Doe, call me on 0400 123 456",
    }
    run_config = build_run_config(state, config)
    assert run_config["metadata"]["customer_id"] == "123"
    assert "environment:test" in run_config["tags"]
    assert "agent_version:9.9.9" in run_config["tags"]
    assert "use_case:customer_service" in run_config["tags"]
    # raw customer message (and any PII within it) is never forwarded to LangSmith metadata
    assert "customer_message" not in run_config["metadata"]
    assert "Jane Doe" not in str(run_config["metadata"])
