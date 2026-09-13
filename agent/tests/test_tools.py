import pytest
from pydantic import ValidationError

from agent.security.authorization import ToolAuthorizationError, authorize_tool_call
from agent.tools.billing import AUTO_APPROVE_CREDIT_THRESHOLD_USD
from agent.tools.registry import SPECIALIST_TOOL_SCOPES, build_default_registry, effective_risk


@pytest.fixture()
def registry():
    return build_default_registry()


def test_registry_has_eleven_enterprise_tools(registry):
    assert set(registry.names()) == {
        "get_customer",
        "get_broadband_service",
        "check_outage",
        "run_network_diagnostics",
        "create_fault_ticket",
        "check_billing_status",
        "apply_billing_credit",
        "run_remote_line_test",
        "schedule_technician_visit",
        "run_equipment_diagnostics",
        "trigger_equipment_reset",
    }


def test_create_fault_ticket_is_high_risk(registry):
    assert registry.get("create_fault_ticket").risk == "high"


@pytest.mark.parametrize(
    "tool_name",
    [
        "get_customer",
        "get_broadband_service",
        "check_outage",
        "run_network_diagnostics",
        "check_billing_status",
        "run_remote_line_test",
        "run_equipment_diagnostics",
    ],
)
def test_low_risk_tools(registry, tool_name):
    assert registry.get(tool_name).risk == "low"


@pytest.mark.parametrize("tool_name", ["schedule_technician_visit", "trigger_equipment_reset"])
def test_unconditionally_high_risk_tools(registry, tool_name):
    spec = registry.get(tool_name)
    assert spec.risk == "high"
    assert spec.risk_override is None


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
        "product": "Home Broadband",
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


def test_check_billing_status_overdue_fixture(registry):
    spec = registry.get("check_billing_status")
    result = spec.handler(spec.input_schema(service_id="BB-111"))
    assert result["service_suspended"] is True
    assert result["overdue_amount"] == 189.50


def test_check_billing_status_default_good_standing(registry):
    spec = registry.get("check_billing_status")
    result = spec.handler(spec.input_schema(service_id="BB-123"))
    assert result == {
        "overdue_amount": 0.0,
        "days_overdue": 0,
        "service_suspended": False,
        "status": "in_good_standing",
    }


def test_apply_billing_credit_deterministic(registry):
    spec = registry.get("apply_billing_credit")
    result = spec.handler(spec.input_schema(service_id="BB-111", amount=50, reason="x"))
    assert result["status"] == "applied"
    assert result["credit_id"].startswith("CR-")


@pytest.mark.parametrize(
    "amount,expected_risk", [(5, "low"), (9.99, "low"), (10, "high"), (500, "high")]
)
def test_apply_billing_credit_risk_threshold(registry, amount, expected_risk):
    spec = registry.get("apply_billing_credit")
    assert effective_risk(spec, {"amount": amount}) == expected_risk


def test_apply_billing_credit_missing_amount_fails_safe_to_high_risk(registry):
    spec = registry.get("apply_billing_credit")
    assert effective_risk(spec, {}) == "high"
    assert effective_risk(spec, {"amount": "not-a-number"}) == "high"


def test_billing_credit_threshold_constant_matches_docs():
    assert AUTO_APPROVE_CREDIT_THRESHOLD_USD == 10.0


def test_run_remote_line_test_faulty_fixture(registry):
    spec = registry.get("run_remote_line_test")
    result = spec.handler(spec.input_schema(service_id="BB-222"))
    assert result["result"] == "line_fault_detected"


def test_run_remote_line_test_default_ok(registry):
    spec = registry.get("run_remote_line_test")
    result = spec.handler(spec.input_schema(service_id="BB-123"))
    assert result["result"] == "line_ok"


def test_schedule_technician_visit_deterministic(registry):
    spec = registry.get("schedule_technician_visit")
    result = spec.handler(spec.input_schema(service_id="BB-222", reason="line fault"))
    assert result["visit_id"].startswith("VIS-")


def test_run_equipment_diagnostics_stale_fixture(registry):
    spec = registry.get("run_equipment_diagnostics")
    result = spec.handler(spec.input_schema(service_id="BB-333"))
    assert result["recommendation"] == "reset_required"


def test_run_equipment_diagnostics_default_no_action(registry):
    spec = registry.get("run_equipment_diagnostics")
    result = spec.handler(spec.input_schema(service_id="BB-123"))
    assert result["recommendation"] == "no_action"


def test_trigger_equipment_reset_deterministic(registry):
    spec = registry.get("trigger_equipment_reset")
    result_a = spec.handler(spec.input_schema(service_id="BB-333"))
    result_b = spec.handler(spec.input_schema(service_id="BB-333"))
    assert result_a["reset_id"] == result_b["reset_id"]


def test_subset_scopes_a_registry_to_only_the_named_tools(registry):
    scoped = registry.subset(SPECIALIST_TOOL_SCOPES["billing"])
    assert set(scoped.names()) == {"check_billing_status", "apply_billing_credit"}
    assert scoped.get("create_fault_ticket") is None
    # Same ToolSpec object, not a copy -- schema/risk/handler stay in sync.
    assert scoped.get("check_billing_status") is registry.get("check_billing_status")


def test_subset_rejects_an_unregistered_name(registry):
    with pytest.raises(ValueError):
        registry.subset(["not_a_real_tool"])


def test_specialist_tool_scopes_cover_every_registered_tool(registry):
    scoped_names = {name for names in SPECIALIST_TOOL_SCOPES.values() for name in names}
    assert scoped_names == set(registry.names())
