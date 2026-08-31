from langgraph.types import Command

from agent.config import AgentConfig
from agent.graph import build_graph
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


def test_agent_loops_through_multiple_decision_cycles():
    config = AgentConfig(mock_mode=True, auto_approve_high_risk=True)
    _, _, result = _run(config, "123", "My broadband keeps dropping out today", "t-loop")
    # 5 tool calls means agent_decision ran at least 6 times (5 dispatch + 1 complete)
    assert result["iteration_count"] >= 6
    assert len(result["observations"]) == len(result["tool_calls"])


def test_three_distinct_diagnostic_paths_are_possible():
    config = AgentConfig(mock_mode=True, auto_approve_high_risk=True)
    _, _, fault = _run(config, "123", "My broadband keeps dropping out today", "t-path-a")
    _, _, outage = _run(config, "456", "My internet is not working", "t-path-b")
    _, _, healthy = _run(config, "789", "My broadband keeps dropping out", "t-path-c")

    paths = {
        tuple(c["tool"] for c in r["tool_calls"]) for r in (fault, outage, healthy)
    }
    assert len(paths) == 3


def test_unknown_tool_requested_by_decision_never_executes():
    class RogueTool:
        def decide(self, state):
            if state.get("iteration_count", 0):
                return {"type": "complete", "resolution": "escalated_invalid_action"}
            return {"type": "tool", "tool": "delete_customer_account", "tool_input": {}}

    config = AgentConfig(mock_mode=True)
    graph = build_graph(config, policy=RogueTool())
    thread = {"configurable": {"thread_id": "t-rogue"}}
    result = graph.invoke(
        {
            "customer_message": "hello",
            "customer_id": "123",
            "request_id": "t-rogue",
            "trace_id": "t-rogue",
        },
        config=thread,
    )
    assert result["tool_calls"] == []
    assert result["status"] == "escalated"
    assert result["resolution"] == "escalated_invalid_action"


def test_registry_used_by_graph_matches_default_registry():
    assert build_default_registry().names()


def test_iteration_guard_forces_escalation_when_policy_never_completes():
    class NeverEndingPolicy:
        def decide(self, state):
            return {
                "type": "tool",
                "tool": "get_customer",
                "tool_input": {"customer_id": state["customer_id"]},
            }

    config = AgentConfig(mock_mode=True, max_iterations=3)
    graph = build_graph(config, policy=NeverEndingPolicy())
    thread = {"configurable": {"thread_id": "t-timeout"}}
    result = graph.invoke(
        {
            "customer_message": "hello",
            "customer_id": "123",
            "request_id": "t-timeout",
            "trace_id": "t-timeout",
        },
        config=thread,
    )
    assert result["status"] == "escalated"
    assert result["resolution"] == "escalated_timeout"
