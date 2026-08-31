import pytest
from pydantic import ValidationError

from agent.security.authorization import ToolAuthorizationError, authorize_tool_call
from agent.tools.registry import build_default_registry


@pytest.fixture()
def registry():
    return build_default_registry()


def test_registry_has_five_enterprise_tools(registry):
    assert set(registry.names()) == {
        "get_customer",
        "get_broadband_service",
        "check_outage",
        "run_network_diagnostics",
        "create_fault_ticket",
    }


def test_create_fault_ticket_is_high_risk(registry):
    assert registry.get("create_fault_ticket").risk == "high"


@pytest.mark.parametrize(
    "tool_name",
    ["get_customer", "get_broadband_service", "check_outage", "run_network_diagnostics"],
)
def test_low_risk_tools(registry, tool_name):
    assert registry.get(tool_name).risk == "low"


def test_authorize_unknown_tool_rejected(registry):
    with pytest.raises(ToolAuthorizationError):
        authorize_tool_call(registry, "delete_customer")


def test_authorize_missing_tool_name_rejected(registry):
    with pytest.raises(ToolAuthorizationError):
        authorize_tool_call(registry, None)


def test_get_customer_known_fixture(registry):
    spec = registry.get("get_customer")
    result = spec.handler(spec.input_schema(customer_id="123"))
    assert result == {"customer_id": "123", "name": "Test Customer", "status": "active"}


def test_get_customer_unknown_id_still_returns_generic_profile(registry):
    spec = registry.get("get_customer")
    result = spec.handler(spec.input_schema(customer_id="999"))
    assert result["status"] == "active"
    assert "999" in result["name"]


def test_get_broadband_service(registry):
    spec = registry.get("get_broadband_service")
    result = spec.handler(spec.input_schema(customer_id="123"))
    assert result == {
        "service_id": "BB-123",
        "product": "TPG Broadband",
        "technology": "FTTP",
        "status": "active",
    }


def test_check_outage_known_outage_fixture(registry):
    spec = registry.get("check_outage")
    result = spec.handler(spec.input_schema(service_id="BB-456"))
    assert result == {"outage": True, "region": "Melbourne", "status": "outage_detected"}


def test_check_outage_default_no_outage(registry):
    spec = registry.get("check_outage")
    result = spec.handler(spec.input_schema(service_id="BB-123"))
    assert result == {"outage": False, "region": "Sydney", "status": "normal"}


def test_diagnostics_healthy_fixture(registry):
    spec = registry.get("run_network_diagnostics")
    result = spec.handler(spec.input_schema(service_id="BB-789"))
    assert result["diagnosis"] == "no_fault_detected"


def test_diagnostics_default_fault(registry):
    spec = registry.get("run_network_diagnostics")
    result = spec.handler(spec.input_schema(service_id="BB-123"))
    assert result["diagnosis"] == "possible_line_fault"


def test_create_fault_ticket_deterministic(registry):
    spec = registry.get("create_fault_ticket")
    result_a = spec.handler(spec.input_schema(service_id="BB-123", diagnostic_summary="x"))
    result_b = spec.handler(spec.input_schema(service_id="BB-123", diagnostic_summary="y"))
    assert result_a["ticket_id"] == result_b["ticket_id"]
    assert result_a["status"] == "created"


def test_tool_input_schema_rejects_missing_fields(registry):
    spec = registry.get("check_outage")
    with pytest.raises(ValidationError):
        spec.input_schema()
