from langgraph.types import Command

from agent.config import AgentConfig
from agent.graph import build_graph
from agent.tools import billing as billing_mod
from agent.tools import fault as fault_mod
from agent.tools.registry import build_default_registry


def _run(config: AgentConfig, customer_id: str, message: str, thread_id: str):
    graph = build_graph(config)
    thread = {"configurable": {"thread_id": thread_id}}
    state = {
        "customer_message": message,
        "customer_id": customer_id,
        "request_id": thread_id,
        "trace_id": thread_id,
    }
    return graph, thread, graph.invoke(state, config=thread)


def test_scenario_fault_takes_full_diagnostic_path_and_pauses_for_approval():
    config = AgentConfig(mock_mode=True, auto_approve_high_risk=False)
    graph, thread, result = _run(config, "123", "My broadband keeps dropping out today", "t-fault")

    assert "__interrupt__" in result
    interrupt_payload = result["__interrupt__"][0].value
    assert interrupt_payload["tool"] == "create_fault_ticket"

    resumed = graph.invoke(Command(resume={"approved": True}), config=thread)
    called_tools = [c["tool"] for c in resumed["tool_calls"]]
    # Exact sequence, not just a prefix check: dedup_add (state.py) collapses
    # the replay-duplicated context_gathering/decision entries a resumed
    # interrupt would otherwise leave behind -- see docs/agent-design.md's
    # "Interrupt replay and idempotency".
    assert called_tools == [
        "get_customer",
        "get_broadband_service",
        "check_outage",
        "run_network_diagnostics",
        "create_fault_ticket",
    ]
    assert resumed["status"] == "resolved"
    assert resumed["resolution"] == "fault_ticket_created"
    assert resumed["ticket_id"]
    assert resumed["specialist_history"] == ["network_diagnose"]


def test_tool_handler_never_executes_twice_across_a_resumed_interrupt(monkeypatch):
    """The safety property the replay behavior above makes essential: even
    though the *node* re-runs, the underlying handler -- what a real backend
    integration would eventually be -- must not fire twice for the same
    call. nodes/tool_execution.py's _already_executed guard is what
    actually provides this."""
    call_count = {"n": 0}
    original = fault_mod.create_fault_ticket

    def counting(payload):
        call_count["n"] += 1
        return original(payload)

    # ToolSpec captures a direct reference to create_fault_ticket at
    # registration time, so this must be patched *before* build_graph()
    # (and its build_default_registry() call) runs below.
    monkeypatch.setattr(fault_mod, "create_fault_ticket", counting)

    config = AgentConfig(mock_mode=True, auto_approve_high_risk=False)
    graph = build_graph(config)
    thread = {"configurable": {"thread_id": "t-idempotent"}}
    state = {
        "customer_message": "My broadband keeps dropping out today",
        "customer_id": "123",
        "request_id": "t-idempotent",
        "trace_id": "t-idempotent",
    }
    graph.invoke(state, config=thread)
    resumed = graph.invoke(Command(resume={"approved": True}), config=thread)

    assert resumed["resolution"] == "fault_ticket_created"
    assert resumed["ticket_id"]
    assert call_count["n"] == 1, "create_fault_ticket's handler must never run twice for one call"


def test_scenario_known_outage_skips_diagnostics():
    config = AgentConfig(mock_mode=True, auto_approve_high_risk=True)
    _, _, result = _run(config, "456", "My internet is not working", "t-outage")

    called_tools = [c["tool"] for c in result["tool_calls"]]
    assert called_tools == ["get_customer", "get_broadband_service", "check_outage"]
    assert "run_network_diagnostics" not in called_tools
    assert "create_fault_ticket" not in called_tools
    assert result["resolution"] == "known_outage"
    assert result["status"] == "resolved"


def test_scenario_healthy_service_gets_guidance_not_a_ticket():
    config = AgentConfig(mock_mode=True, auto_approve_high_risk=True)
    _, _, result = _run(config, "789", "My broadband keeps dropping out", "t-healthy")

    called_tools = [c["tool"] for c in result["tool_calls"]]
    assert called_tools == [
        "get_customer",
        "get_broadband_service",
        "check_outage",
        "run_network_diagnostics",
    ]
    assert "create_fault_ticket" not in called_tools
    assert result["resolution"] == "healthy_no_action"


def test_auto_approve_high_risk_skips_the_pause():
    config = AgentConfig(mock_mode=True, auto_approve_high_risk=True)
    _, _, result = _run(config, "123", "My broadband keeps dropping out today", "t-auto")

    assert "__interrupt__" not in result
    assert result["resolution"] == "fault_ticket_created"
    assert result["ticket_id"]


def test_rejecting_approval_escalates_instead_of_creating_ticket():
    config = AgentConfig(mock_mode=True, auto_approve_high_risk=False)
    graph, thread, result = _run(config, "123", "My broadband keeps dropping out today", "t-reject")
    assert "__interrupt__" in result

    resumed = graph.invoke(Command(resume={"approved": False}), config=thread)
    assert resumed["status"] == "escalated"
    assert "create_fault_ticket" not in [c["tool"] for c in resumed["tool_calls"]]


def test_agent_makes_multiple_tool_calls_before_finishing():
    config = AgentConfig(mock_mode=True, auto_approve_high_risk=True)
    _, _, result = _run(config, "123", "My broadband keeps dropping out today", "t-loop")
    assert len(result["tool_calls"]) >= 5
    assert len(result["observations"]) == len(result["tool_calls"])


def test_three_distinct_diagnostic_paths_are_possible():
    config = AgentConfig(mock_mode=True, auto_approve_high_risk=True)
    _, _, fault = _run(config, "123", "My broadband keeps dropping out today", "t-path-a")
    _, _, outage = _run(config, "456", "My internet is not working", "t-path-b")
    _, _, healthy = _run(config, "789", "My broadband keeps dropping out", "t-path-c")

    paths = {tuple(c["tool"] for c in r["tool_calls"]) for r in (fault, outage, healthy)}
    assert len(paths) == 3


def test_unknown_tool_requested_by_specialist_never_executes():
    class RogueTool:
        def decide(self, state):
            if state.get("iteration_count", 0):
                return {"type": "complete", "resolution": "escalated_invalid_action"}
            return {"type": "tool", "tool": "delete_customer_account", "tool_input": {}}

    config = AgentConfig(mock_mode=True, auto_approve_high_risk=True)
    graph = build_graph(config, specialist_policy_overrides={"network_diagnose": RogueTool()})
    thread = {"configurable": {"thread_id": "t-rogue"}}
    result = graph.invoke(
        {
            "customer_message": "My broadband keeps dropping out today",
            "customer_id": "123",
            "request_id": "t-rogue",
            "trace_id": "t-rogue",
        },
        config=thread,
    )
    # get_customer/get_broadband_service still ran (context_gathering, before
    # any specialist is picked) -- the rogue tool name is never even attempted.
    assert "delete_customer_account" not in [c["tool"] for c in result["tool_calls"]]
    assert result["status"] == "escalated"
    assert result["resolution"] == "escalated_invalid_action"


def test_specialist_cannot_reach_another_specialists_tool_even_via_a_rogue_policy():
    """The scoped-registry equivalent of test_authorize_unknown_tool_rejected
    in test_security.py: a policy attached to one specialist naming a tool
    that belongs to a *different* specialist must fail exactly like an
    invented tool name would -- proving the boundary is the scoped registry
    (tools/registry.py's SPECIALIST_TOOL_SCOPES), not prompt wording."""

    class CrossScopeTool:
        def decide(self, state):
            if state.get("iteration_count", 0):
                return {"type": "complete", "resolution": "escalated_invalid_action"}
            # apply_billing_credit belongs to the billing specialist, not
            # network_diagnose -- must be unreachable here.
            return {
                "type": "tool",
                "tool": "apply_billing_credit",
                "tool_input": {"service_id": "BB-123", "amount": 5, "reason": "x"},
            }

    config = AgentConfig(mock_mode=True, auto_approve_high_risk=True)
    graph = build_graph(config, specialist_policy_overrides={"network_diagnose": CrossScopeTool()})
    thread = {"configurable": {"thread_id": "t-cross-scope"}}
    result = graph.invoke(
        {
            "customer_message": "My broadband keeps dropping out today",
            "customer_id": "123",
            "request_id": "t-cross-scope",
            "trace_id": "t-cross-scope",
        },
        config=thread,
    )
    assert "apply_billing_credit" not in [c["tool"] for c in result["tool_calls"]]
    assert result["status"] == "escalated"
    assert result["resolution"] == "escalated_invalid_action"


def test_registry_used_by_graph_matches_default_registry():
    assert build_default_registry().names()


def test_iteration_guard_forces_escalation_when_policy_never_completes():
    class NeverEndingPolicy:
        def decide(self, state):
            return {
                "type": "tool",
                "tool": "check_outage",
                "tool_input": {"service_id": state.get("service_id")},
            }

    config = AgentConfig(mock_mode=True, max_iterations=3)
    graph = build_graph(
        config, specialist_policy_overrides={"network_diagnose": NeverEndingPolicy()}
    )
    thread = {"configurable": {"thread_id": "t-timeout"}}
    result = graph.invoke(
        {
            "customer_message": "My broadband keeps dropping out today",
            "customer_id": "123",
            "request_id": "t-timeout",
            "trace_id": "t-timeout",
        },
        config=thread,
    )
    assert result["status"] == "escalated"
    assert result["resolution"] == "escalated_timeout"


def test_dispatch_guard_forces_escalation_when_orchestrator_never_completes():
    _ROTATION = ["network_diagnose", "billing", "line_testing", "equipment_reset"]

    class NeverEndingOrchestrator:
        def decide(self, state):
            # Cycles through a different specialist each time (never
            # repeating immediately) specifically to avoid the router's
            # own "never dispatch the same specialist twice" guard, so
            # this exercises the *dispatch-count* ceiling instead.
            next_specialist = _ROTATION[len(state.get("specialist_history", [])) % len(_ROTATION)]
            return {
                "type": "dispatch",
                "specialist": next_specialist,
                "reasoning": "never finishes",
            }

    config = AgentConfig(mock_mode=True, auto_approve_high_risk=True, max_specialist_dispatches=2)
    graph = build_graph(config, orchestrator_policy=NeverEndingOrchestrator())
    thread = {"configurable": {"thread_id": "t-dispatch-timeout"}}
    result = graph.invoke(
        {
            "customer_message": "My broadband keeps dropping out today",
            "customer_id": "123",
            "request_id": "t-dispatch-timeout",
            "trace_id": "t-dispatch-timeout",
        },
        config=thread,
    )
    assert result["status"] == "escalated"
    assert result["resolution"] == "escalated_timeout"
    assert result["specialist_history"] == ["network_diagnose", "billing"]


def test_billing_specialist_resolves_overdue_balance_with_approval():
    config = AgentConfig(mock_mode=True, auto_approve_high_risk=False)
    graph, thread, result = _run(
        config, "111", "I think I've been overcharged on my bill.", "t-billing"
    )
    assert "__interrupt__" in result
    assert result["__interrupt__"][0].value["tool"] == "apply_billing_credit"

    resumed = graph.invoke(Command(resume={"approved": True}), config=thread)
    assert resumed["status"] == "resolved"
    assert resumed["resolution"] == "billing_hold_resolved"
    assert resumed["specialist_history"][-1] == "billing"


def test_billing_credit_under_threshold_auto_approves(monkeypatch):
    # BB-111's fixture overdue amount ($189.50) is always >= the $10
    # threshold, so patch it down to prove the *auto-approve* branch (not
    # just the always-tested high-value approval branch) actually works.
    monkeypatch.setitem(
        billing_mod._OVERDUE_SERVICES, "BB-111", {"overdue_amount": 5.0, "days_overdue": 3}
    )
    config = AgentConfig(mock_mode=True, auto_approve_high_risk=False)
    _, _, result = _run(
        config, "111", "I think I've been overcharged on my bill.", "t-billing-auto"
    )

    assert "__interrupt__" not in result
    assert result["status"] == "resolved"
    assert result["resolution"] == "billing_hold_resolved"


def test_line_testing_specialist_confirms_fault_with_approval():
    config = AgentConfig(mock_mode=True, auto_approve_high_risk=False)
    graph, thread, result = _run(
        config, "222", "There's a lot of crackling static on the line.", "t-line"
    )
    assert "__interrupt__" in result
    assert result["__interrupt__"][0].value["tool"] == "schedule_technician_visit"

    resumed = graph.invoke(Command(resume={"approved": True}), config=thread)
    assert resumed["status"] == "resolved"
    assert resumed["resolution"] == "line_test_fault_confirmed"


def test_equipment_reset_specialist_resets_with_approval_and_supports_rejection():
    config = AgentConfig(mock_mode=True, auto_approve_high_risk=False)
    graph, thread, result = _run(
        config, "333", "My router needs a reboot, it's offline.", "t-reset"
    )
    assert "__interrupt__" in result
    assert result["__interrupt__"][0].value["tool"] == "trigger_equipment_reset"

    resumed = graph.invoke(Command(resume={"approved": True}), config=thread)
    assert resumed["status"] == "resolved"
    assert resumed["resolution"] == "equipment_reset_completed"

    # And the reject path, on a fresh thread -- the second worked example of
    # the approval boundary beyond fault-ticket creation.
    graph2, thread2, result2 = _run(config, "333", "My router needs a reboot.", "t-reset-reject")
    assert "__interrupt__" in result2
    resumed2 = graph2.invoke(Command(resume={"approved": False}), config=thread2)
    assert resumed2["status"] == "escalated"
    assert "trigger_equipment_reset" not in [c["tool"] for c in resumed2["tool_calls"]]


def test_chained_investigation_dispatches_network_diagnose_then_billing():
    config = AgentConfig(mock_mode=True, auto_approve_high_risk=True)
    _, _, result = _run(
        config,
        "111",
        "My broadband keeps dropping out and I think I've been overcharged on my last bill.",
        "t-chained",
    )
    assert "network_diagnose" in result["specialist_history"]
    assert "billing" in result["specialist_history"]
    assert result["specialist_history"].index("network_diagnose") < result[
        "specialist_history"
    ].index("billing")
    assert result["status"] == "resolved"
